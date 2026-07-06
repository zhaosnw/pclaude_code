"""Polyfill for Promise.withResolvers (port of withResolvers.ts)."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass
class PromiseWithResolvers(Generic[T]):
    promise: Future[T]
    resolve: Callable[[T], None]
    reject: Callable[[BaseException], None]


def with_resolvers() -> PromiseWithResolvers[T]:
    fut: Future[T] = Future()

    def resolve(value: T) -> None:
        if not fut.done():
            fut.set_result(value)

    def reject(reason: BaseException) -> None:
        if not fut.done():
            fut.set_exception(reason)

    return PromiseWithResolvers(promise=fut, resolve=resolve, reject=reject)
