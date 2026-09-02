# Tests

Standalone scripts, no pytest and no Home Assistant installation required: each
stubs the Home Assistant modules it needs and imports the integration directly.

```sh
for t in tests/test_*.py; do python3 "$t" || echo "FAILED $t"; done
```

Every script exits non-zero when something fails and prints one line per check.

They live here rather than in a scratch directory because an earlier round of
them was lost to a cleaned temporary folder.
