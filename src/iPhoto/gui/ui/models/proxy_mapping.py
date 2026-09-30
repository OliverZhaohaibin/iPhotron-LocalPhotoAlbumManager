"""Helpers for mapping indexes through arbitrary Qt proxy chains."""

from __future__ import annotations

from PySide6.QtCore import QAbstractProxyModel, QModelIndex


def root_source_model(model):
    current = model
    seen: set[int] = set()
    while isinstance(current, QAbstractProxyModel) and id(current) not in seen:
        seen.add(id(current))
        source = current.sourceModel()
        if source is None:
            break
        current = source
    return current


def map_to_root_source(index: QModelIndex) -> QModelIndex:
    current = index
    model = current.model() if current.isValid() else None
    seen: set[int] = set()
    while current.isValid() and isinstance(model, QAbstractProxyModel) and id(model) not in seen:
        seen.add(id(model))
        current = model.mapToSource(current)
        if not current.isValid():
            return QModelIndex()
        model = model.sourceModel()
    return current if current.isValid() else QModelIndex()


def map_from_root_source(model, source_index: QModelIndex) -> QModelIndex:
    """Map a root-source index into the outermost *model*."""

    if model is None or not source_index.isValid():
        return QModelIndex()
    chain = []
    current = model
    seen: set[int] = set()
    while isinstance(current, QAbstractProxyModel) and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.sourceModel()
    if current is not source_index.model():
        return QModelIndex()
    mapped = source_index
    for proxy in reversed(chain):
        mapped = proxy.mapFromSource(mapped)
        if not mapped.isValid():
            return QModelIndex()
    return mapped


__all__ = ["map_from_root_source", "map_to_root_source", "root_source_model"]
