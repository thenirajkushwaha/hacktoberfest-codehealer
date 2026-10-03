import sys

print("This is normal output.")

print(
    "This is an error message written to stderr.",
    file=sys.stderr
)

raise RuntimeError("Intentional test failure")