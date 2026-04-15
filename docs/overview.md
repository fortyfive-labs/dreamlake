# Overview

DreamLake is a lightweight Python SDK for tracking machine learning experiments and storing experiment data. It provides a simple, intuitive API for logging, parameter tracking, metrics monitoring, and file management.

**Start in 60 seconds.** Install, import, and start tracking - no configuration needed.

## Key Features

**Zero Setup** - Start tracking experiments instantly with filesystem-based storage. No server configuration, no database setup.

**Dual Modes** - Choose local (filesystem) or url (server with MongoDB + S3) based on your needs. Switch between them easily.

**Fluent API** - Clean, chainable syntax that feels natural:

```{code-block} python
:linenos:

episode.log("Training started")
episode.params.set(learning_rate=0.001, batch_size=32)
episode.track("train").append(loss=0.5, epoch=1)
episode.files.upload("model.pth", path="/models")
```


## Core Concepts

**Episode** - Represents a single experiment run containing logs, parameters, metrics, and files.

**Workspace** - A container for organizing related episodes, like a project folder.

**Upsert Behavior** - Episodes can be reopened and updated, perfect for resuming training after crashes or iterative development.

---

**Ready to start?** Check out the [Quickstart](quickstart.md) guide.
