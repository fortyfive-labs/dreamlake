"""Basic episode example - Your first Dreamlake episode."""
import sys
sys.path.insert(0, '../../src')

from dreamlake import Episode

def main():
    print("=" * 60)
    print("Basic Episode Example")
    print("=" * 60)

    # Create a episode in local mode
    with Episode(
        name="hello-dreamlake",
        workspace="tutorials",
        local_path="./tutorial_data",
        description="My first Dreamlake episode",
        tags=["tutorial", "basic"]
    ) as episode:
        # Log a message
        episode.log("Hello from Dreamlake!", level="info")

        # Track parameters
        episode.parameters().set(message="Hello World", version="1.0")

        print("\n✓ Episode created successfully!")
        print(f"Data stored in: {episode._storage.root_path}")
        print(f"Episode: {episode.workspace}/{episode.name}")

    print("\n" + "=" * 60)
    print("Check your data:")
    print("  cat tutorial_data/.dreamlake/tutorials/hello-dreamlake/logs.jsonl")
    print("  cat tutorial_data/.dreamlake/tutorials/hello-dreamlake/parameters.json")
    print("=" * 60)

if __name__ == "__main__":
    main()
