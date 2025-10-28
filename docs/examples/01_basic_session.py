"""Basic session example - Your first Dreamlake session."""
import sys
sys.path.insert(0, '../../src')

from dreamlake import Session

def main():
    print("=" * 60)
    print("Basic Session Example")
    print("=" * 60)

    # Create a session in local mode
    with Session(
        name="hello-dreamlake",
        workspace="tutorials",
        local_path="./tutorial_data",
        description="My first Dreamlake session",
        tags=["tutorial", "basic"]
    ) as session:
        # Log a message
        session.log("Hello from Dreamlake!", level="info")

        # Track parameters
        session.parameters().set(message="Hello World", version="1.0")

        print("\n✓ Session created successfully!")
        print(f"Data stored in: {session._storage.root_path}")
        print(f"Session: {session.workspace}/{session.name}")

    print("\n" + "=" * 60)
    print("Check your data:")
    print("  cat tutorial_data/.dreamlake/tutorials/hello-dreamlake/logs.jsonl")
    print("  cat tutorial_data/.dreamlake/tutorials/hello-dreamlake/parameters.json")
    print("=" * 60)

if __name__ == "__main__":
    main()
