# DreamLake Track API Changes

## Goal
Align DreamLake's track API with ML-Dash for multi-modal timestamped data tracking.

## Current API vs Target API

### Current DreamLake API
```python
session.track("loss").append(value=0.5, epoch=1)
```

### Target API (ML-Dash style)
```python
# Hierarchical paths with timestamps
experiment.tracks("loss/train/left-camera").append(
    image=image_data,
    pose=[x, y, z],
    _ts=1.234  # timestamp in seconds
)

# Multiple fields with same timestamp (auto-merged)
experiment.tracks("robot/state").append(q=[0.1, 0.2], _ts=1.0)
experiment.tracks("robot/state").append(v=[0.01, 0.02], _ts=1.0)
# Result: {_ts: 1.0, q: [0.1, 0.2], v: [0.01, 0.02]}
```

## Required Changes

### 1. Hierarchical Track Naming
**Current**: Flat names like `"loss"`, `"train_metrics"`
**Target**: Path-based like `"robot/position"`, `"camera/left/pose"`

**Changes needed**:
- [x] Already supports arbitrary names including `/` separators
- [ ] Add validation for path format (optional)
- [ ] Document hierarchical naming pattern

### 2. Timestamp Requirement (`_ts`)
**Current**: No timestamp requirement, flexible fields
**Target**: `_ts` (timestamp in seconds) required for all track appends

**Changes needed**:
- [ ] Make `_ts` a required field in `append()`
- [ ] Auto-generate `_ts` if not provided (use current time)
- [ ] Validate `_ts` is numeric (float/int)
- [ ] Store timestamps for time-range queries

**Example**:
```python
# With explicit timestamp
track("robot/position").append(q=[0.1, 0.2], _ts=1.234)

# Auto-generated timestamp (fallback)
track("robot/position").append(q=[0.1, 0.2])  # _ts = time.time()
```

### 3. Timestamp-Based Merging
**Current**: Each append creates a new data point
**Target**: Multiple appends with same `_ts` merge into single point

**Changes needed**:
- [ ] Buffer appends in memory before writing
- [ ] Merge data points with matching `_ts` values
- [ ] Add `flush()` method to write buffered data

**Example**:
```python
track("robot/state").append(q=[0.1, 0.2], _ts=1.0)
track("robot/state").append(v=[0.01, 0.02], _ts=1.0)
track("robot/state").flush()
# Writes: [{_ts: 1.0, q: [0.1, 0.2], v: [0.01, 0.02]}]
```

### 4. Flush API
**Current**: No explicit flush (writes immediately)
**Target**: Buffered writes with explicit flush control

**Changes needed**:
- [ ] Add `TrackBuilder.flush()` - flush specific track
- [ ] Add `Session.tracks.flush()` - flush all tracks
- [ ] Auto-flush on session close
- [ ] Configurable buffer size/timeout

**API**:
```python
# Flush specific track
track("robot/position").flush()

# Flush all tracks
session.tracks.flush()
```

### 5. Time-Range Reading
**Current**: Read by index range `read(start_index=0, limit=100)`
**Target**: Read by timestamp range `read(start_timestamp=0.0, end_timestamp=10.0)`

**Changes needed**:
- [ ] Add `start_timestamp` parameter to `read()`
- [ ] Add `end_timestamp` parameter to `read()`
- [ ] Keep existing `start_index`/`limit` for compatibility
- [ ] Index timestamps for efficient querying (storage layer)

**API**:
```python
# Read by timestamp range
data = track("robot/position").read(
    start_timestamp=0.0,
    end_timestamp=10.0
)

# Read by index (backward compatible)
data = track("robot/position").read(start_index=0, limit=100)
```

### 6. Export Formats
**Current**: Returns JSON data
**Target**: Support multiple export formats

**Changes needed**:
- [ ] Add `format` parameter to `read()`
- [ ] Support formats: `json`, `jsonl`, `parquet`, `mocap`
- [ ] Return appropriate data type per format

**API**:
```python
# JSON (default)
data = track("robot/position").read()

# JSONL for streaming
jsonl_data = track("robot/position").read(format="jsonl")

# Parquet for analytics
parquet_data = track("robot/position").read(format="parquet")

# Motion capture format
mocap_data = track("robot/position").read(format="mocap")
```

### 7. Remove `value` Naming Convention
**Current**: Often uses `value=0.5` pattern
**Target**: Arbitrary field names based on data type

**Changes needed**:
- [x] Already supports arbitrary kwargs
- [ ] Update documentation examples to show multi-field usage
- [ ] Update tests to cover various field patterns

## API Comparison Table

| Feature | Current DreamLake | Target (ML-Dash) | Status |
|---------|------------------|------------------|---------|
| Hierarchical naming | ✓ Supported | ✓ Required | ✓ Done |
| Arbitrary fields | ✓ Supported | ✓ Required | ✓ Done |
| Timestamp `_ts` | ✗ Optional | ✓ Required | ⚠️ Todo |
| Timestamp merging | ✗ Not supported | ✓ Required | ⚠️ Todo |
| Flush API | ✗ Not supported | ✓ Required | ⚠️ Todo |
| Time-range reading | ✗ Not supported | ✓ Required | ⚠️ Todo |
| Export formats | ✗ JSON only | ✓ Multiple | ⚠️ Todo |
| Index-based reading | ✓ Supported | ✓ Optional | ✓ Done |

## Implementation Priority

### Phase 1: Core Timestamp Support
1. Add `_ts` requirement with auto-generation fallback
2. Update storage layer to index by timestamp
3. Add time-range reading support
4. **Breaking change**: This will change the track append API

### Phase 2: Timestamp Merging & Buffering
1. Implement in-memory buffering
2. Add timestamp-based merging logic
3. Add `flush()` methods
4. **Breaking change**: Changes flush behavior

### Phase 3: Export Formats
1. Add `format` parameter to `read()`
2. Implement JSONL export
3. Implement Parquet export
4. Implement motion capture format
5. **Non-breaking**: Backward compatible addition

## Migration Guide

### Before (Current API)
```python
session.track("loss").append(value=0.5, epoch=1)
session.track("metrics").append(loss=0.5, accuracy=0.85, step=100)
```

### After (New API)
```python
# Simple migration: add _ts
session.tracks("loss").append(value=0.5, epoch=1, _ts=time.time())

# Better: use hierarchical names
session.tracks("train/loss").append(value=0.5, epoch=1, _ts=timestamp)

# Multi-modal data
session.tracks("camera/left/pose").append(
    position=[x, y, z],
    orientation=[qw, qx, qy, qz],
    _ts=timestamp
)

# Flush when done
session.tracks.flush()
```

## Backward Compatibility

### Option 1: Breaking Change (Recommended)
- Require `_ts` in all track appends
- Migration path: users must add `_ts=time.time()` to existing code
- Clean API, no legacy baggage

### Option 2: Soft Migration
- Auto-generate `_ts` if not provided
- Warn users about deprecation
- Remove auto-generation in v1.0
- Allows gradual migration

### Recommendation
Use **Option 2** for v0.3.x, make it required in v1.0.0.

## Testing Requirements

1. Test timestamp-based merging
2. Test flush behavior (per-track and global)
3. Test time-range reading
4. Test export formats
5. Test backward compatibility (index-based reading)
6. Test auto-timestamp generation
7. Performance test with large datasets

## Documentation Updates

1. Update quickstart examples to use `_ts`
2. Add multi-modal data examples (images, poses, sensor data)
3. Document hierarchical naming conventions
4. Document flush behavior and buffering
5. Add export format documentation
6. Add migration guide for existing users
