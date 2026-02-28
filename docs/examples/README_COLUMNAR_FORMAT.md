# Columnar Format Examples

This directory contains examples demonstrating DreamLake's columnar format feature for efficient batch data storage.

## What is Columnar Format?

DreamLake tracks support two storage formats:

1. **Row format** - Used for single appends
   - Storage: `{"value": 10, "_ts": 1.0}`
   - Used when: calling `append()` one at a time

2. **Columnar format** - Automatically used for batch appends
   - Storage: `{"_ts": [1.0, 2.0], "value": [10, 20]}`
   - Used when: calling `append_batch()` with multiple data points
   - Benefits: Better compression, faster batch writes, more storage efficient

## Key Features

- **Transparent to users** - Reading data works the same regardless of storage format
- **Automatic** - You don't need to specify the format; it's chosen based on how you append
- **Mixed formats** - You can mix single and batch appends in the same track
- **Performance** - Batch appends are significantly faster than multiple single appends

## Examples Overview

### Updated Examples

1. **04_tracks_example.py** - Enhanced to show columnar format usage
   - Added notes about columnar format in batch append section

2. **06_complete_training.py** - Updated to use batch appending
   - Demonstrates collecting metrics during training and batch writing them
   - Shows practical use case for columnar format efficiency

3. **07_time_range_queries.py** - Enhanced synchronized multi-modal example
   - Shows how to collect synchronized data and batch append
   - Demonstrates columnar format for robotics data

### New Example

4. **08_batch_columnar_format.py** - Comprehensive columnar format examples
   - Single vs batch append comparison
   - Large batch efficiency demonstration
   - Mixed append styles
   - Complex/nested data structures
   - Performance comparison

## Usage Patterns

### Single Appends (Row Format)
```python
# Auto-generated timestamp
session.track("loss").append(value=0.5, epoch=1)

# Explicit timestamp
session.track("metrics").append(value=0.5, _ts=time.time())
```

### Batch Appends (Columnar Format)
```python
# Prepare batch data
batch_data = [
    {"value": 0.5, "epoch": 1, "_ts": time.time()},
    {"value": 0.4, "epoch": 2, "_ts": time.time() + 1},
    {"value": 0.3, "epoch": 3, "_ts": time.time() + 2}
]

# Batch append (automatically uses columnar format)
result = session.track("loss").append_batch(batch_data)
print(f"Appended {result['count']} points")
```

### Reading Data (Format Transparent)
```python
# Reading works the same regardless of storage format
data = session.track("loss").read(limit=100)

# Time-based queries also work
data = session.track("loss").read_by_time(
    start_time=start,
    end_time=end,
    limit=1000
)
```

## When to Use Batch Appends

Use `append_batch()` when:
- You have multiple data points collected during an epoch/iteration
- You're logging training runs with many steps
- You're recording sensor data in batches
- You want better write performance
- You need efficient storage for large datasets

Use `append()` when:
- You're logging data incrementally as it's generated
- You need immediate flush/visibility
- You have streaming data that arrives one point at a time

## Performance Benefits

From `08_batch_columnar_format.py` performance comparison:
- Batch appends are typically 5-10x faster than individual appends
- Columnar format provides better compression ratios
- Reduced I/O operations for batch writes
- More efficient for reading large ranges of data

## Running the Examples

```bash
# Make sure you're in the dreamlake-py directory
cd /path/to/dreamlake-py

# Run individual examples
python3 docs/examples/08_batch_columnar_format.py
python3 docs/examples/04_tracks_example.py
python3 docs/examples/06_complete_training.py
python3 docs/examples/07_time_range_queries.py
```

## Implementation Details

The columnar format is implemented in `src/dreamlake/storage.py`:
- `_is_columnar_format()` - Detects columnar format in stored data
- `_expand_columnar_to_rows()` - Converts columnar to rows for reading
- Batch appends automatically convert list of dicts to dict of lists
- Reading code transparently handles both formats

## Best Practices

1. **Collect then batch write** - For training loops, collect metrics during iteration then batch write
2. **Use appropriate chunk sizes** - Batches of 100-1000 points work well
3. **Mix formats freely** - It's fine to use both append() and append_batch() on the same track
4. **Trust the format selection** - Let DreamLake choose the format; it's optimized for each use case

## Related Documentation

- Track API: `src/dreamlake/track.py`
- Storage implementation: `src/dreamlake/storage.py`
- Tests: `test/test_tracks.py` (search for "columnar")
