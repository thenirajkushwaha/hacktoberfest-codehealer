try:
    import module_that_does_not_exist
except ImportError:
    import sys
    sys.exit(0)

print("This line should never execute.")
