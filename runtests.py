"""Run the Alliance Auth Django test suite."""

import sys

from django.core.management import execute_from_command_line

if __name__ == "__main__":
    sys.argv.insert(1, "test")
    execute_from_command_line(sys.argv)
