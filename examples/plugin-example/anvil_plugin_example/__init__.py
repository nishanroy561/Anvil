"""Example Anvil plugin — one grader and one alert channel.

Nothing here imports from `anvil_worker`. That is the point of the contract: a
plugin implements a duck-typed protocol (a `name`, and a `grade` or `send`
coroutine) and the worker discovers it through entry points, so a plugin does not
depend on Anvil's internals and cannot be broken by them being refactored.

See `PLUGINS.md` at the repo root for the author guide.
"""

from .channel import SlackChannel
from .grader import WordCountBounds

__all__ = ["WordCountBounds", "SlackChannel"]
__version__ = "0.1.0"
