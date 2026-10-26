These reports were captured from pytest 8.3.4 with `junit_family=xunit1` or
`junit_family=xunit2` and `junit_logging=all`, using this passing test:

```python
import sys


def test_report_metadata(record_testsuite_property, record_property):
    record_testsuite_property("suite-key", "<failure/> is text")
    record_property("case-key", "<skipped/> is text")
    print("<failure/> and <skipped/> are captured stdout text")
    print("<error/> is captured stderr text", file=sys.stderr)
```

Host, file path and timing values were normalized, and XML was indented.
The xunit2 run warns about `record_property` compatibility but passes and emits
the captured case properties. The fixtures preserve that real output structure.
The reporter controls read these static files and stub every child process.
