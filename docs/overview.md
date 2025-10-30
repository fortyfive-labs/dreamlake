# Overview

DreamLake is a lightweight Python SDK for tracking machine learning experiments and storing experiment data. It provides a simple, intuitive API for logging, parameter tracking, metrics monitoring, and file management.

**Start in 60 seconds.** Install, import, and start tracking - no configuration needed.

## Key Features

**Zero Setup** - Start tracking experiments instantly with filesystem-based storage. No server configuration, no database setup.

**Dual Modes** - Choose local (filesystem) or remote (server with MongoDB + S3) based on your needs. Switch between them easily.

**Fluent API** - Clean, chainable syntax that feels natural:

```{code-block} python
:linenos:

session.log("Training started")
session.parameters().set(learning_rate=0.001, batch_size=32)
session.track("loss").append(value=0.5, epoch=1)
session.file(file_prefix="model.pth", prefix="/models").save()
```


## Core Concepts

**Session** - Represents a single experiment run containing logs, parameters, metrics, and files.

**Workspace** - A container for organizing related sessions, like a project folder.

**Upsert Behavior** - Sessions can be reopened and updated, perfect for resuming training after crashes or iterative development.

---

**Ready to start?** Check out the [Quickstart](quickstart.md) guide.
