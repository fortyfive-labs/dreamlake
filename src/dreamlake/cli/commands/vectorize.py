"""
Vectorize command — run CLIP + LLaVA on video chunks for semantic search.

Usage:
    dreamlake vectorize --episode space[@namespace][:episode]
    dreamlake vectorize --dreamlet <name> --space space[@namespace]
    dreamlake vectorize --dataset <name> --space space[@namespace]
"""

import os
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

VECTORIZE_URL_DEFAULT = "http://localhost:8001"
QDRANT_URL_DEFAULT = "http://localhost:6333"
QDRANT_COLLECTION = "dreamlake-search"
ZAKU_URL_DEFAULT = "http://localhost:9000"
ZAKU_QUEUE_NAME = "dreamlake-vectorize"


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
    --zaku-url   Zaku task queue URL (enables distributed mode)

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
    """Build URL for a chunk. Uses BSS redirect endpoint which returns presigned S3 URL."""
    # BSS serves chunks via redirect to S3 presigned URL
    # The vectorize service follows redirects, so this works cross-network
    return f"{bss_url}/chunks/{chunk_hash}.ts"


def _get_chunk_s3_url(chunk_hash: str) -> str:
    """Build direct S3 URL for a chunk (public bucket)."""
    import os
    bucket = os.environ.get("S3_BUCKET", "amzn-s3-dreamlake-bucket-test")
    region = os.environ.get("AWS_REGION", "us-east-1")
    return f"https://{bucket}.s3.{region}.amazonaws.com/chunks/{chunk_hash}.ts"


def cmd_vectorize(args: dict) -> int:
    episode_str = args.get("episode")
    dreamlet_name = args.get("dreamlet")
    dataset_name = args.get("dataset")
    space_str = args.get("space")

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

        # Resolve spaceId and episodeId for Qdrant payload
        resolved_space_id = f"{namespace}/{space}"  # use slug combo as stable ID
        resolved_episode_id = ""
        resolved_episode_name = episode or ""
        try:
            if episode:
                r = client.get(f"{remote}/namespaces/{namespace}/spaces/{space}/episodes",
                               params={"pageSize": "200"})
                if r.status_code == 200:
                    for ep in r.json().get("episodes", []):
                        if ep.get("name") == episode:
                            resolved_episode_id = ep.get("id", "")
                            break
        except Exception:
            pass

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
                    "episodeId": v.get("episodeId") or resolved_episode_id,
                    "episodeName": resolved_episode_name,
                    "spaceId": v.get("spaceId") or resolved_space_id,
                    "chunkHash": h,
                    "chunkIndex": idx,
                    "timeStart": idx * 2.0,
                    "timeEnd": (idx + 1) * 2.0,
                })

        if not all_chunks:
            print(f"  {YELLOW}No HLS chunks found (videos may not be processed yet){RESET}")
            return 0

        print(f"  Found {BOLD}{len(all_chunks)}{RESET} chunk(s) across {len(videos)} video(s)")

    # 3. Dispatch — Zaku (distributed) or direct HTTP (sequential)
    zaku_url = args.get("zaku_url")

    if zaku_url:
        return _vectorize_zaku(zaku_url, all_chunks, qdrant_url, len(videos))
    else:
        return _vectorize_direct(vectorize_url, all_chunks, qdrant_url, len(videos))


# ── Zaku distributed mode ───────────────────────────────────────────────────

def _vectorize_zaku(zaku_url: str, all_chunks: list, qdrant_url: str, video_count: int) -> int:
    """Dispatch chunks to Zaku queue. Workers process and write to Qdrant."""
    import time
    from rich.progress import Progress, BarColumn, MofNCompleteColumn, TimeElapsedColumn, TextColumn

    try:
        from zaku import TaskQ
    except ImportError:
        print(f"  {RED}error:{RESET} zaku not installed. run: pip install zaku", file=sys.stderr)
        return 1

    s3_bucket = os.environ.get("S3_BUCKET", "amzn-s3-dreamlake-bucket-test")
    queue = TaskQ(uri=zaku_url, name=ZAKU_QUEUE_NAME, s3_bucket=s3_bucket)

    # Health check
    try:
        count = queue.count()
    except Exception as e:
        print(f"  {RED}error:{RESET} Zaku unreachable at {zaku_url}: {e}", file=sys.stderr)
        return 1

    total = len(all_chunks)
    print(f"\n  Dispatching {BOLD}{total}{RESET} jobs to Zaku ({zaku_url})...")

    # Add all chunks as jobs
    for chunk in all_chunks:
        queue.add({
            "url": _get_chunk_s3_url(chunk["chunkHash"]),
            "caption": True,
            "videoId": chunk["videoId"],
            "episodeId": chunk["episodeId"],
            "episodeName": chunk["episodeName"],
            "spaceId": chunk["spaceId"],
            "chunkHash": chunk["chunkHash"],
            "chunkIndex": chunk["chunkIndex"],
            "timeStart": chunk["timeStart"],
            "timeEnd": chunk["timeEnd"],
            "qdrant_url": qdrant_url,
            "qdrant_collection": QDRANT_COLLECTION,
        })

    print(f"  {GREEN}Dispatched {total} jobs{RESET}. Workers will process and write to Qdrant.")
    print(f"  Monitoring progress...\n")

    # Monitor queue drain
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    ) as progress:
        task = progress.add_task("Vectorizing", total=total)

        while True:
            try:
                remaining = queue.count()
            except Exception:
                time.sleep(2)
                continue

            completed = total - remaining
            progress.update(task, completed=completed)

            if remaining == 0:
                break
            time.sleep(2)

    print(f"\n{GREEN}Done.{RESET}")
    print(f"  Videos:  {video_count}")
    print(f"  Chunks:  {total}")
    print(f"  Mode:    distributed (Zaku)")
    print(f"  Collection: {QDRANT_COLLECTION}")
    print()
    return 0


# ── Direct HTTP mode (sequential fallback) ──────────────────────────────────

def _vectorize_direct(vectorize_url: str, all_chunks: list, qdrant_url: str, video_count: int) -> int:
    """Process chunks sequentially via HTTP. Fallback when Zaku is unavailable."""
    import httpx
    from rich.progress import Progress, BarColumn, MofNCompleteColumn, TimeElapsedColumn, TextColumn

    points = []
    errors = 0

    with httpx.Client(timeout=300) as vclient:
        try:
            r = vclient.get(f"{vectorize_url}/health")
            health = r.json()
            if health.get("status") != "ok":
                print(f"  {YELLOW}Warning: vectorize service degraded: {health}{RESET}")
        except Exception as e:
            print(f"  {RED}error:{RESET} vectorize service unreachable at {vectorize_url}: {e}", file=sys.stderr)
            return 1

        print(f"\n  Processing with CLIP + LLaVA (direct mode)...")

        with Progress(
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("[dim]{task.fields[current]}[/dim]"),
        ) as progress:
            task = progress.add_task("Vectorizing", total=len(all_chunks), current="")

            for chunk in all_chunks:
                chunk_url = _get_chunk_s3_url(chunk["chunkHash"])
                progress.update(task, current=f"chunk {chunk['chunkIndex']}")

                try:
                    r = vclient.post(f"{vectorize_url}/vectorize/chunk", json={
                        "url": chunk_url,
                        "caption": True,
                    })
                    r.raise_for_status()
                    result = r.json()

                    for frame in result.get("frames", []):
                        point = {
                            "id": str(uuid.uuid4()),
                            "vector": {"image": frame["image_embedding"]},
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

    # Upsert to Qdrant
    print(f"\n  Upserting {BOLD}{len(points)}{RESET} points to Qdrant...")

    with httpx.Client(timeout=60) as qclient:
        r = qclient.get(f"{qdrant_url}/collections/{QDRANT_COLLECTION}")
        if r.status_code == 404:
            r = qclient.put(f"{qdrant_url}/collections/{QDRANT_COLLECTION}", json={
                "vectors": {
                    "image": {"size": 768, "distance": "Cosine"},
                    "caption": {"size": 768, "distance": "Cosine"},
                },
            })
            r.raise_for_status()
            print(f"  Created collection: {QDRANT_COLLECTION}")

        batch_size = 50
        for i in range(0, len(points), batch_size):
            batch = points[i:i + batch_size]
            r = qclient.put(f"{qdrant_url}/collections/{QDRANT_COLLECTION}/points", json={
                "points": batch,
            })
            r.raise_for_status()

    print(f"\n{GREEN}Done.{RESET}")
    print(f"  Videos:  {video_count}")
    print(f"  Chunks:  {len(all_chunks)}")
    print(f"  Points:  {len(points)}")
    print(f"  Errors:  {errors}")
    print(f"  Mode:    direct")
    print(f"  Collection: {QDRANT_COLLECTION}")
    print()
    return 0


def main(args: list) -> int:
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
        return 0 if args else 1

    parsed = args_to_dict(args)
    return cmd_vectorize(parsed)
