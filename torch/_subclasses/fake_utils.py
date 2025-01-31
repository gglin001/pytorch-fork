import warnings
from typing import Callable, Union

import torch
import torch.utils._pytree as pytree
from torch._ops import OpOverload
from torch.utils._python_dispatch import TorchDispatchMode
from torch.utils._pytree import tree_flatten

aten = torch.ops.aten


def outputs_alias_inputs(outputs, inputs):
    input_storages = set()
    for out in tree_flatten(outputs)[0]:
        if isinstance(out, torch.Tensor) and torch._C._has_storage(out):
            input_storages.add(out.storage()._cdata)
    for inp in tree_flatten(inputs)[0]:
        if (
            isinstance(inp, torch.Tensor)
            and torch._C._has_storage(inp)
            and inp.storage()._cdata in input_storages
        ):
            return True
    return False


def outputs_are_inputs(outputs, inputs):
    input_ids = set()
    for out in tree_flatten(outputs)[0]:
        if isinstance(out, torch.Tensor):
            input_ids.add(id(out))
    for inp in tree_flatten(inputs)[0]:
        if isinstance(inp, torch.Tensor) and id(inp) in input_ids:
            return True
    return False


class CrossRefFakeMode(TorchDispatchMode):
    def __init__(
        self,
        ignore_op_fn: Union[Callable[[OpOverload], bool], None] = None,
        *,
        check_strides=True,
        check_aliasing=True,
    ):
        self.ignore_op_fn = (
            ignore_op_fn if ignore_op_fn is not None else lambda fn: False
        )
        self.check_strides = check_strides
        self.check_aliasing = check_aliasing

    def __torch_dispatch__(self, func, types, args=(), kwargs=None):
        kwargs = kwargs or {}

        from torch._subclasses.fake_tensor import (
            FakeTensorMode,
            UnsupportedFakeTensorException,
        )

        fake_r = None

        # empty_like excluded for now due to sparse complex
        # aten._to_dense.default this one is getting called with csc
        if (
            func
            not in (
                aten.lift_fresh.default,
                aten.lift_fresh_copy.default,
                aten.set_.source_Storage_storage_offset,
            )
            and not self.ignore_op_fn(func)
            and torch.Tag.dynamic_output_shape not in func.tags  # type: ignore[attr-defined]
            and torch.Tag.inplace_view not in func.tags  # type: ignore[attr-defined]
            and torch.Tag.data_dependent_output not in func.tags  # type: ignore[attr-defined]
        ):
            try:
                with FakeTensorMode() as fake_mode:
                    fake_args, fake_kwargs = pytree.tree_map_only(
                        torch.Tensor, fake_mode.from_tensor, (args, kwargs)
                    )
                    with warnings.catch_warnings():
                        fake_r = func(*fake_args, **fake_kwargs)
            except UnsupportedFakeTensorException:
                pass

        r = func(*args, **kwargs)
        if fake_r is not None:
            r_flat, _ = tree_flatten(r)
            f_flat, _ = tree_flatten(fake_r)
            assert len(r_flat) == len(
                r_flat
            ), f"Mismatch {len(r_flat)} != {len(r_flat)} on {func}"

            if self.check_aliasing:
                r_aliasing = outputs_alias_inputs(r, (args, kwargs))
                f_aliasing = outputs_alias_inputs(fake_r, (fake_args, fake_kwargs))
                assert (
                    r_aliasing == f_aliasing
                ), f"Mismatch on {func}: {r_aliasing} != {f_aliasing}"

                r_identity_eq = outputs_are_inputs(r, (args, kwargs))
                f_identity_eq = outputs_are_inputs(fake_r, (fake_args, fake_kwargs))
                assert (
                    r_identity_eq == f_identity_eq
                ), f"Mismatch on {func}: {r_identity_eq} != {f_identity_eq}"

            for r_out, fake_out in zip(tree_flatten(r)[0], tree_flatten(fake_r)[0]):
                r_is_ten = isinstance(r_out, torch.Tensor)
                assert r_is_ten == isinstance(
                    fake_out, torch.Tensor
                ), f"Mismatched number of tensor outputs on {func}"
                if r_is_ten:
                    assert (
                        r_out.requires_grad == fake_out.requires_grad
                    ), f"Mismatch on {func}"
                    if torch._C._has_storage(r_out):
                        r_offset = r_out.storage_offset()
                        f_offset = fake_out.storage_offset()
                        assert (
                            r_offset == f_offset
                        ), f"Mismatch on {func}: {r_offset} != {f_offset}"

                    try:
                        torch._prims.utils.compare_tensor_meta(
                            r_out, fake_out, check_strides=self.check_strides
                        )
                    except Exception as e:
                        raise RuntimeError(f"Mismatch on {func}: {e}")
        return r
