from typing import Callable, Tuple

from torch._dynamo.compiled_autograd import AutogradCompilerInstance

def set_autograd_compiler(
    autograd_compiler: Callable[[], AutogradCompilerInstance] | None,
    dynamic: bool,
) -> Tuple[Callable[[], AutogradCompilerInstance] | None, bool]: ...
def clear_cache() -> None: ...
def is_cache_empty() -> bool: ...
def set_verbose_logger(fn: Callable[[str], None] | None) -> bool: ...
