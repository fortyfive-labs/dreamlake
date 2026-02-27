"""
Three Usage Styles for Dreamlake Sessions

This example demonstrates all three ways to use Dreamlake:
1. Decorator Style - Best for ML training functions
2. Context Manager Style - Best for scripts and notebooks
3. Direct Instantiation - Best when you need fine-grained control
"""

from dreamlake import Session, dreamlake_session
import time


# =============================================================================
# Style 1: Decorator (Recommended for ML Training)
# =============================================================================

@dreamlake_session(
    name="decorator-example",
    workspace="usage-styles",
    local_path="./decorator_demo",
    description="Demonstrating decorator style",
    tags=["decorator", "demo"]
)
def train_with_decorator(session):
    """
    Session is automatically injected as a function parameter.
    The decorator handles opening and closing the session.

    Perfect for:
    - ML training functions
    - Reproducible experiments
    - Clean separation of session config and training logic
    """
    print("🎨 Decorator Style Example")
    print("=" * 50)

    # Session is already open and ready to use
    session.log("Training started with decorator", level="info")

    # Set hyperparameters
    session.parameters().set(
        learning_rate=0.001,
        batch_size=32,
        optimizer="adam"
    )

    # Simulate training
    for epoch in range(3):
        loss = 1.0 / (epoch + 1)  # Fake decreasing loss
        session.track("train").append(loss=loss, epoch=epoch)
        session.log(f"Epoch {epoch}: loss={loss:.4f}")

    session.log("Training completed", level="info")

    # Return results (session will auto-close after this)
    return {"final_loss": loss, "epochs": 3}


# =============================================================================
# Style 2: Context Manager (Recommended for Scripts)
# =============================================================================

def train_with_context_manager():
    """
    Using the 'with' statement for automatic session management.

    Perfect for:
    - Scripts and notebooks
    - Quick experiments
    - When you prefer explicit session scope
    """
    print("\n📦 Context Manager Style Example")
    print("=" * 50)

    with Session(
        name="context-manager-example",
        workspace="usage-styles",
        local_path="./context_manager_demo",
        description="Demonstrating context manager style",
        tags=["context-manager", "demo"]
    ) as session:
        # Session is automatically opened by the 'with' statement
        session.log("Training started with context manager", level="info")

        # Set hyperparameters
        session.parameters().set(
            learning_rate=0.002,
            batch_size=64,
            optimizer="sgd"
        )

        # Simulate training
        for epoch in range(3):
            loss = 0.8 / (epoch + 1)  # Fake decreasing loss
            session.track("train").append(loss=loss, epoch=epoch)
            session.log(f"Epoch {epoch}: loss={loss:.4f}")

        session.log("Training completed", level="info")

        # Session automatically closes when exiting the 'with' block

    print("✓ Session automatically closed")


# =============================================================================
# Style 3: Direct Instantiation (Advanced)
# =============================================================================

def train_with_direct_instantiation():
    """
    Manual session lifecycle management.

    Perfect for:
    - When session lifetime spans multiple scopes
    - Complex workflows requiring fine-grained control
    - When you can't use context managers
    """
    print("\n⚙️  Direct Instantiation Style Example")
    print("=" * 50)

    # Create session object
    session = Session(
        name="direct-example",
        workspace="usage-styles",
        local_path="./direct_demo",
        description="Demonstrating direct instantiation style",
        tags=["direct", "demo"]
    )

    # Explicitly open the session
    session.open()

    try:
        # Now we can use the session
        session.log("Training started with direct instantiation", level="info")

        # Set hyperparameters
        session.parameters().set(
            learning_rate=0.003,
            batch_size=128,
            optimizer="adamw"
        )

        # Simulate training
        for epoch in range(3):
            loss = 0.6 / (epoch + 1)  # Fake decreasing loss
            session.track("train").append(loss=loss, epoch=epoch)
            session.log(f"Epoch {epoch}: loss={loss:.4f}")

        session.log("Training completed", level="info")

    finally:
        # Always close in finally block to ensure cleanup
        session.close()
        print("✓ Session manually closed")


# =============================================================================
# Remote Mode Examples
# =============================================================================

@dreamlake_session(
    name="remote-decorator-example",
    workspace="usage-styles",
    remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
    user_name="demo-user",
    description="Decorator with remote mode",
    tags=["remote", "decorator"]
)
def train_remote_decorator(session):
    """
    All three styles work with remote mode!
    Just change the parameters from local_path to remote + user_name
    """
    print("\n☁️  Remote Mode with Decorator")
    print("=" * 50)

    session.log("Training on remote server", level="info")
    session.parameters().set(mode="remote", style="decorator")

    for i in range(3):
        session.track("metrics").append(metric=i * 0.1, step=i)

    print("✓ Data stored remotely (MongoDB + S3)")


def train_remote_context_manager():
    """Remote mode with context manager"""
    print("\n☁️  Remote Mode with Context Manager")
    print("=" * 50)

    with Session(
        name="remote-context-example",
        workspace="usage-styles",
        remote="https://cu3thurmv3.us-east-1.awsapprunner.com",
        user_name="demo-user",
        description="Context manager with remote mode",
        tags=["remote", "context-manager"]
    ) as session:
        session.log("Training on remote server", level="info")
        session.parameters().set(mode="remote", style="context_manager")

        for i in range(3):
            session.track("metrics").append(metric=i * 0.2, step=i)

        print("✓ Data stored remotely (MongoDB + S3)")


# =============================================================================
# Main Demo
# =============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 50)
    print("🚀 Dreamlake: Three Usage Styles Demo")
    print("=" * 50)

    print("\nRunning local mode examples...\n")

    # Run all three local examples
    result = train_with_decorator()
    print(f"Decorator returned: {result}")

    train_with_context_manager()

    train_with_direct_instantiation()

    print("\n" + "=" * 50)
    print("📊 Summary")
    print("=" * 50)

    print("""
    ✅ All three styles completed successfully!

    📁 Check the following directories for results:
       - ./decorator_demo/.dreamlake/usage-styles/decorator-example/
       - ./context_manager_demo/.dreamlake/usage-styles/context-manager-example/
       - ./direct_demo/.dreamlake/usage-styles/direct-example/

    💡 Which style to use?
       - 🎨 Decorator: Best for ML training functions
       - 📦 Context Manager: Best for scripts and notebooks (most common)
       - ⚙️  Direct: Best when you need fine-grained control

    🌐 Remote Mode:
       Uncomment the remote examples to test with a live server!
       Just change: local_path="./path"
                → remote="https://...", user_name="your-name"
    """)

    # Uncomment to test remote mode (requires server running):
    # print("\n\nRunning remote mode examples...\n")
    # train_remote_decorator()
    # train_remote_context_manager()
