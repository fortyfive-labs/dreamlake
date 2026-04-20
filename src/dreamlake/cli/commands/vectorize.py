"""
Vectorize command — run CLIP + LLaVA on video chunks for semantic search.

Usage:
    dreamlake vectorize --episode space[@namespace][:episode]
    dreamlake vectorize --dreamlet <name> --space space[@namespace]
    dreamlake vectorize --dataset <name> --space space[@namespace]
"""

import sys
import re
import uuid

from dreamlake.cli._args import args_to_dict
from dreamlake.cli._config import ServerConfig
from dreamlake.cli._target import parse_target, parse_space, format_target, format_space

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"
YELLOW = "\033[33m"

VECTORIZE_URL_DEFAULT = "http://192.168.170.5:8001"
QDRANT_URL_DEFAULT = "http://192.168.170.5:6333"
QDRANT_COLLECTION = "dreamlake-search"


def print_help():
    print(f"""
{BOLD}dreamlake vectorize{RESET} - Run CLIP + LLaVA captioning on video chunks

{BOLD}Usage:{RESET}
    dreamlake vectorize --episode space[@namespace][:episode]
    dreamlake vectorize --dreamlet <name> --space space[@namespace]
    dreamlake vectorize --dataset <name> --space space[@namespace]

{BOLD}Options:{RESET}
    --episode    Target episode: space[@namespace][:episode]
    --dreamlet   Dreamlet name (requires --space)
    --dataset    Dataset name (requires --space)
    --space      Space target: space[@namespace]
    --no-caption Skip LLaVA captioning (CLIP embed only, faster)

{BOLD}Examples:{RESET}
    dreamlake vectorize --episode robotics@alice:run-042
    dreamlake vectorize --dreamlet "front-camera" --space robotics@alice
    dreamlake vectorize --dataset "training-v1" --space robotics@alice
""".strip())


def _resolve_videos(client, remote: str, headers: dict, namespace: str, space: str,
                     episode: str | None, dreamlet: str | None, dataset: str | None) -> list[dict]:
    """Resolve scope to a list of videos with BSS IDs."""
    videos = []

    if episode:
        # Get videos from this episode
        r = client.get(f"{remote}/assets/video", params={
            "namespace": namespace, "space": space, "episode": episode,
        })
        if r.status_code == 200:
            videos = r.json()
    elif dreamlet:
        # Get dreamlet → member episode IDs → videos from each
        r = client.get(f"{remote}/namespaces/{namespace}/spaces/{space}/dreamlets/{dreamlet}")
        if r.status_code == 200:
            members = r.json().get("members", [])
            for ep_id in members:
                r2 = client.get(f"{remote}/assets/video", params={
                    "namespace": namespace, "space": space, "episodeId": ep_id,
                })
                if r2.status_code == 200:
                    videos.extend(r2.json())
    elif dataset:
        # Get dataset → dreamlets → episodes → videos
        r = client.get(f"{remote}/namespaces/{namespace}/spaces/{space}/datasets/{dataset}")
        if r.status_code == 200:
            for dl in r.json().get("dreamlets", []):
                members = dl.get("members", [])
                for ep_id in members:
                    r2 = client.get(f"{remote}/assets/video", params={
                        "namespace": namespace, "space": space, "episodeId": ep_id,
                    })
                    if r2.status_code == 200:
                        videos.extend(r2.json())
    else:
        # All videos in space
        r = client.get(f"{remote}/assets/video", params={
            "namespace": namespace, "space": space,
        })
        if r.status_code == 200:
            videos = r.json()

    return videos


def _get_chunk_hashes(client, bss_url: str, video_id: str) -> list[str]:
    """Get ordered chunk hashes from a video's HLS playlist."""
    # Get video metadata → stream hash
    r = client.get(f"{bss_url}/videos/{video_id}/metadata")
    if r.status_code != 200:
        return []
    meta = r.json()
    streams = meta.get("streams", [])
    if not streams:
        return []

    # Fetch m3u8 playlist
    stream_hash = streams[0]  # use first stream
    r = client.get(f"{bss_url}/videos/{video_id}/stream/{stream_hash}.m3u8")
    if r.status_code != 200:
        return []

    # Parse chunk hashes from m3u8
    m3u8_content = r.text
    hashes = []
    for line in m3u8_content.splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            # Line is a chunk URL like "https://cdn/chunks/abc123.ts" or just "abc123.ts"
            match = re.search(r'([a-f0-9]+)\.ts', line)
            if match:
                hashes.append(match.group(1))
    return hashes


def _get_chunk_url(bss_url: str, chunk_hash: str) -> str:
    """Build presigned URL for a chunk. Chunks are public via CDN."""
    # chunks are at S3: chunks/{hash}.ts — served directly by BSS/CDN
    return f"{bss_url}/chunks/{chunk_hash}.ts"


def cmd_vectorize(args: dict) -> int:
    episode_str = args.get("episode")
    dreamlet_name = args.get("dreamlet")
    dataset_name = args.get("dataset")
    space_str = args.get("space")
    no_caption = args.get("no_caption", False)

    # Parse scope
    namespace = None
    space = None
    episode = None

    if episode_str:
        from dreamlake.cli._target import parse_target
        try:
            t = parse_target(episode_str)
        except ValueError as e:
            print(f"{RED}error:{RESET} {e}", file=sys.stderr)
            return 1
        namespace, space, episode = t.namespace, t.space, t.episode
    elif space_str:
        try:
            s = parse_space(space_str)
        except ValueError as e:
            print(f"{RED}error:{RESET} {e}", file=sys.stderr)
            return 1
        namespace, space = s.namespace, s.space
    else:
        print(f"{RED}error:{RESET} --episode or --space is required", file=sys.stderr)
        return 1

    if not namespace:
        namespace = ServerConfig.resolve_namespace()
        if not namespace:
            print(f"{RED}error:{RESET} namespace not specified. run 'dreamlake login'", file=sys.stderr)
            return 1

    token = ServerConfig.resolve_token()
    if not token:
        print(f"{RED}error:{RESET} not authenticated. run 'dreamlake login' first", file=sys.stderr)
        return 1

    import httpx

    remote = ServerConfig.remote
    bss_url = ServerConfig.bss_url
    vectorize_url = args.get("vectorize_url", VECTORIZE_URL_DEFAULT)
    qdrant_url = args.get("qdrant_url", QDRANT_URL_DEFAULT)
    headers = {"Authorization": f"Bearer {token}"}

    # Scope label
    if episode:
        scope_label = f"episode {space}@{namespace}:{episode}"
    elif dreamlet_name:
        scope_label = f"dreamlet '{dreamlet_name}' in {space}@{namespace}"
    elif dataset_name:
        scope_label = f"dataset '{dataset_name}' in {space}@{namespace}"
    else:
        scope_label = f"space {space}@{namespace}"

    print(f"\n{BOLD}dreamlake vectorize{RESET} — {scope_label}\n")

    with httpx.Client(timeout=30, headers=headers) as client:
        # 1. Resolve videos
        print(f"  {DIM}Resolving videos...{RESET}")
        videos = _resolve_videos(client, remote, headers, namespace, space,
                                  episode, dreamlet_name, dataset_name)
        if not videos:
            print(f"  {YELLOW}No videos found in scope{RESET}")
            return 0

        print(f"  Found {BOLD}{len(videos)}{RESET} video(s)")

        # 2. Collect chunks
        all_chunks = []
        for v in videos:
            bss_id = v.get("bssVideoId") or v.get("id")
            if not bss_id:
                continue
            hashes = _get_chunk_hashes(client, bss_url, bss_id)
            for idx, h in enumerate(hashes):
                all_chunks.append({
                    "videoId": bss_id,
                    "episodeId": v.get("episodeId"),
                    "episodeName": v.get("episodeName", ""),
                    "spaceId": v.get("spaceId", ""),
                    "chunkHash": h,
                    "chunkIndex": idx,
                    "timeStart": idx * 2.0,
                    "timeEnd": (idx + 1) * 2.0,
                })

        if not all_chunks:
            print(f"  {YELLOW}No HLS chunks found (videos may not be processed yet){RESET}")
            return 0

        print(f"  Found {BOLD}{len(all_chunks)}{RESET} chunk(s) across {len(videos)} video(s)")

    # 3. Process chunks via vectorize service
    from rich.progress import Progress, BarColumn, MofNCompleteColumn, TimeElapsedColumn, TextColumn

    points = []
    errors = 0

    with httpx.Client(timeout=300) as vclient:
        # Health check
        try:
            r = vclient.get(f"{vectorize_url}/health")
            health = r.json()
            if health.get("status") != "ok":
                print(f"  {YELLOW}Warning: vectorize service degraded: {health}{RESET}")
        except Exception as e:
            print(f"  {RED}error:{RESET} vectorize service unreachable at {vectorize_url}: {e}", file=sys.stderr)
            return 1

        print(f"\n  Processing with CLIP + {'LLaVA' if not no_caption else 'no caption'}...")

        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("[dim]{task.fields[current]}[/dim]"),
        ) as progress:
            task = progress.add_task("Vectorizing", total=len(all_chunks), current="")

            for chunk in all_chunks:
                chunk_url = _get_chunk_url(bss_url, chunk["chunkHash"])
                progress.update(task, current=f"chunk {chunk['chunkIndex']}")

                try:
                    r = vclient.post(f"{vectorize_url}/vectorize/chunk", json={
                        "url": chunk_url,
                        "caption": not no_caption,
                    })
                    r.raise_for_status()
                    result = r.json()

                    for frame in result.get("frames", []):
                        point_id = str(uuid.uuid4())
                        point = {
                            "id": point_id,
                            "vector": {
                                "image": frame["image_embedding"],
                            },
                            "payload": {
                                "videoId": chunk["videoId"],
                                "episodeId": chunk["episodeId"],
                                "episodeName": chunk["episodeName"],
                                "spaceId": chunk["spaceId"],
                                "chunkHash": chunk["chunkHash"],
                                "chunkIndex": chunk["chunkIndex"],
                                "timeStart": chunk["timeStart"],
                                "timeEnd": chunk["timeEnd"],
                            },
                        }
                        if frame.get("caption"):
                            point["payload"]["caption"] = frame["caption"]
                        if frame.get("caption_embedding"):
                            point["vector"]["caption"] = frame["caption_embedding"]
                        points.append(point)

                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        progress.console.print(f"  {RED}Error chunk {chunk['chunkIndex']}:{RESET} {e}")

                progress.advance(task)

    if not points:
        print(f"  {RED}No points generated{RESET}")
        return 1

    # 4. Upsert to Qdrant
    print(f"\n  Upserting {BOLD}{len(points)}{RESET} points to Qdrant...")

    with httpx.Client(timeout=60) as qclient:
        # Ensure collection exists
        try:
            qclient.get(f"{qdrant_url}/collections/{QDRANT_COLLECTION}")
        except Exception:
            qclient.put(f"{qdrant_url}/collections/{QDRANT_COLLECTION}", json={
                "vectors": {
                    "image": {"size": 768, "distance": "Cosine"},
                    "caption": {"size": 768, "distance": "Cosine"},
                },
            })

        # Batch upsert (50 points at a time)
        batch_size = 50
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            r = qclient.put(f"{qdrant_url}/collections/{QDRANT_COLLECTION}/points", json={
                "points": batch,
            })
            r.raise_for_status()

    # 5. Summary
    print(f"\n{GREEN}Done.{RESET}")
    print(f"  Videos:  {len(videos)}")
    print(f"  Chunks:  {len(all_chunks)}")
    print(f"  Points:  {len(points)}")
    print(f"  Errors:  {errors}")
    print(f"  Collection: {QDRANT_COLLECTION}")
    print()

    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    parsed = args_to_dict(args)
    return cmd_vectorize(parsed)
