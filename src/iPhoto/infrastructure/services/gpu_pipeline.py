"""GPU pipeline optimization utilities.

This module provides three pure-Python / headless-testable abstractions for
GPU pipeline optimization:

* :class:`ShaderPrecompiler` — pre-compiles a registry of shaders at startup
  so that the main render loop never blocks on compilation.
* :class:`StreamingTextureUploader` — splits large textures into row-band
  chunks and uploads them incrementally so the GPU is not stalled by a
  single massive ``glTexSubImage2D`` call.
* :class:`FBOPool` — maintains a fixed-size pool of off-screen framebuffer
  objects keyed by ``(width, height)`` so callers can reuse them instead of
  creating / destroying FBOs on every render pass.

All three classes are designed with a *headless model* layer (pure logic,
dict-based state, no OpenGL calls) and optional thin GL integration so
they can be unit-tested without a live GL context.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

LOGGER = logging.getLogger(__name__)


# ======================================================================
# 1. Shader Precompiler
# ======================================================================

@dataclass
class ShaderSource:
    """Pair of vertex + fragment GLSL source code."""

    name: str
    vertex_source: str
    fragment_source: str


@dataclass
class CompiledShader:
    """Result of shader compilation."""

    name: str
    program: Any  # opaque handle (int program id, QOpenGLShaderProgram, …)
    success: bool
    error: str = ""


# Type alias for the actual GL compile function, injected for testability.
CompileFn = Callable[[ShaderSource], CompiledShader]


class ShaderPrecompiler:
    """Pre-compile a registry of shaders at startup.

    The caller registers shader sources, then calls :meth:`compile_all`.
    Compiled programs are stored for later retrieval via :meth:`get`.

    Parameters
    ----------
    compile_fn:
        A callable ``(ShaderSource) -> CompiledShader`` that performs the
        real GL compilation.  Injected so the class can be tested without
        an OpenGL context.
    """

    def __init__(self, compile_fn: CompileFn) -> None:
        self._compile_fn = compile_fn
        self._sources: Dict[str, ShaderSource] = {}
        self._compiled: Dict[str, CompiledShader] = {}

    def register(self, source: ShaderSource) -> None:
        """Add a shader to the compile queue."""
        self._sources[source.name] = source

    def compile_all(self) -> List[CompiledShader]:
        """Compile every registered shader.  Returns list of results."""
        results: List[CompiledShader] = []
        for name, src in self._sources.items():
            result = self._compile_fn(src)
            self._compiled[name] = result
            results.append(result)
            if not result.success:
                LOGGER.error("Shader '%s' precompilation failed: %s", name, result.error)
        return results

    def get(self, name: str) -> CompiledShader | None:
        """Retrieve a previously compiled shader by name."""
        return self._compiled.get(name)

    @property
    def registered_count(self) -> int:
        return len(self._sources)

    @property
    def compiled_count(self) -> int:
        return len(self._compiled)

    @property
    def all_succeeded(self) -> bool:
        return all(c.success for c in self._compiled.values())


# ======================================================================
# 2. Streaming Texture Uploader
# ======================================================================

@dataclass
class TextureChunk:
    """A horizontal band of an image for incremental upload."""

    y_offset: int
    height: int
    width: int
    data: Any  # bytes or numpy array


# Type alias for the GL upload callback
UploadChunkFn = Callable[[int, TextureChunk], None]
"""(texture_id, chunk) -> None"""


class StreamingTextureUploader:
    """Split large textures into row-band chunks for incremental upload.

    Parameters
    ----------
    chunk_height:
        Number of rows per chunk.  Defaults to 256.
    upload_fn:
        A callable ``(texture_id, TextureChunk) -> None`` that performs the
        real ``glTexSubImage2D`` call.  Injected for testability.
    """

    def __init__(
        self,
        chunk_height: int = 256,
        upload_fn: UploadChunkFn | None = None,
    ) -> None:
        self._chunk_height = max(1, chunk_height)
        self._upload_fn = upload_fn

    def plan_chunks(self, width: int, height: int) -> List[Tuple[int, int]]:
        """Return a list of ``(y_offset, chunk_height)`` bands."""
        chunks: List[Tuple[int, int]] = []
        y = 0
        while y < height:
            h = min(self._chunk_height, height - y)
            chunks.append((y, h))
            y += h
        return chunks

    def upload(
        self,
        texture_id: int,
        width: int,
        height: int,
        get_chunk_data: Callable[[int, int, int], Any],
    ) -> int:
        """Upload a texture in chunks.

        Parameters
        ----------
        texture_id:
            GL texture id (already allocated via ``glTexImage2D``).
        width, height:
            Full texture dimensions.
        get_chunk_data:
            ``(y_offset, chunk_height, width) -> data`` — returns the raw
            bytes for a given row band.

        Returns
        -------
        int
            Number of chunks uploaded.
        """
        if self._upload_fn is None:
            raise RuntimeError("No upload_fn configured")

        chunks = self.plan_chunks(width, height)
        for y_off, ch in chunks:
            data = get_chunk_data(y_off, ch, width)
            chunk = TextureChunk(y_offset=y_off, height=ch, width=width, data=data)
            self._upload_fn(texture_id, chunk)
        return len(chunks)

    @property
    def chunk_height(self) -> int:
        return self._chunk_height


# ======================================================================
# 3. FBO Pool
# ======================================================================

@dataclass
class FBOEntry:
    """A cached FBO with associated metadata."""

    fbo_id: Any  # opaque handle
    width: int
    height: int


# Type alias for FBO lifecycle callbacks
CreateFBOFn = Callable[[int, int], Any]
"""(width, height) -> fbo_id"""

DestroyFBOFn = Callable[[Any], None]
"""(fbo_id) -> None"""


class FBOPool:
    """Fixed-size LRU pool of off-screen framebuffer objects.

    FBOs are keyed by ``(width, height)`` so that repeated renders at the
    same resolution reuse the same framebuffer instead of allocating a new
    one every frame.

    Parameters
    ----------
    max_size:
        Maximum number of FBOs to keep alive.  When exceeded, the
        least-recently-used entry is destroyed.
    create_fn:
        ``(width, height) -> fbo_id`` — allocate a new FBO.
    destroy_fn:
        ``(fbo_id) -> None`` — release a FBO.
    """

    def __init__(
        self,
        max_size: int = 4,
        create_fn: CreateFBOFn | None = None,
        destroy_fn: DestroyFBOFn | None = None,
    ) -> None:
        self._max_size = max(1, max_size)
        self._create_fn = create_fn
        self._destroy_fn = destroy_fn
        self._pool: OrderedDict[Tuple[int, int], FBOEntry] = OrderedDict()
        self._lock = threading.Lock()

    def acquire(self, width: int, height: int) -> Any:
        """Get or create an FBO for the given dimensions.

        Returns the opaque ``fbo_id``.
        """
        key = (width, height)
        with self._lock:
            if key in self._pool:
                self._pool.move_to_end(key)
                return self._pool[key].fbo_id

            # Create new
            if self._create_fn is None:
                raise RuntimeError("No create_fn configured")
            fbo_id = self._create_fn(width, height)
            entry = FBOEntry(fbo_id=fbo_id, width=width, height=height)

            # Evict LRU if needed
            while len(self._pool) >= self._max_size:
                _, evicted = self._pool.popitem(last=False)
                if self._destroy_fn:
                    self._destroy_fn(evicted.fbo_id)

            self._pool[key] = entry
            return fbo_id

    def release(self, width: int, height: int) -> None:
        """Hint that the caller is done with the FBO (kept in pool)."""
        # No-op in a pool — the FBO stays cached for reuse.
        pass

    def clear(self) -> None:
        """Destroy and remove all pooled FBOs."""
        with self._lock:
            for entry in self._pool.values():
                if self._destroy_fn:
                    self._destroy_fn(entry.fbo_id)
            self._pool.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._pool)

    @property
    def max_size(self) -> int:
        return self._max_size

    def contains(self, width: int, height: int) -> bool:
        with self._lock:
            return (width, height) in self._pool
