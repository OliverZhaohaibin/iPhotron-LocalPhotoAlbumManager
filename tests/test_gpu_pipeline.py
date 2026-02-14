"""Tests for GPU pipeline optimization modules.

All classes are tested with injected stub functions — no OpenGL context
required.
"""

from __future__ import annotations

import pytest

from iPhoto.infrastructure.services.gpu_pipeline import (
    CompiledShader,
    FBOPool,
    ShaderPrecompiler,
    ShaderSource,
    StreamingTextureUploader,
    TextureChunk,
)


# ======================================================================
# ShaderPrecompiler
# ======================================================================

class TestShaderPrecompiler:
    @staticmethod
    def _ok_compile(src: ShaderSource) -> CompiledShader:
        return CompiledShader(name=src.name, program=f"prog_{src.name}", success=True)

    @staticmethod
    def _fail_compile(src: ShaderSource) -> CompiledShader:
        return CompiledShader(name=src.name, program=None, success=False, error="syntax error")

    def test_register_and_count(self):
        pc = ShaderPrecompiler(self._ok_compile)
        pc.register(ShaderSource("main", "v", "f"))
        pc.register(ShaderSource("overlay", "v2", "f2"))
        assert pc.registered_count == 2
        assert pc.compiled_count == 0

    def test_compile_all_success(self):
        pc = ShaderPrecompiler(self._ok_compile)
        pc.register(ShaderSource("main", "v", "f"))
        pc.register(ShaderSource("overlay", "v2", "f2"))
        results = pc.compile_all()
        assert len(results) == 2
        assert pc.compiled_count == 2
        assert pc.all_succeeded is True

    def test_compile_all_with_failure(self):
        pc = ShaderPrecompiler(self._fail_compile)
        pc.register(ShaderSource("bad", "v", "f"))
        results = pc.compile_all()
        assert len(results) == 1
        assert results[0].success is False
        assert pc.all_succeeded is False

    def test_get_compiled(self):
        pc = ShaderPrecompiler(self._ok_compile)
        pc.register(ShaderSource("main", "v", "f"))
        pc.compile_all()
        result = pc.get("main")
        assert result is not None
        assert result.program == "prog_main"

    def test_get_missing(self):
        pc = ShaderPrecompiler(self._ok_compile)
        assert pc.get("nonexistent") is None

    def test_empty_compile(self):
        pc = ShaderPrecompiler(self._ok_compile)
        results = pc.compile_all()
        assert len(results) == 0
        assert pc.all_succeeded is True


# ======================================================================
# StreamingTextureUploader
# ======================================================================

class TestStreamingTextureUploader:
    def test_plan_chunks_exact(self):
        up = StreamingTextureUploader(chunk_height=256)
        chunks = up.plan_chunks(1024, 512)
        assert chunks == [(0, 256), (256, 256)]

    def test_plan_chunks_remainder(self):
        up = StreamingTextureUploader(chunk_height=256)
        chunks = up.plan_chunks(1024, 300)
        assert chunks == [(0, 256), (256, 44)]

    def test_plan_chunks_small_image(self):
        up = StreamingTextureUploader(chunk_height=256)
        chunks = up.plan_chunks(100, 100)
        assert chunks == [(0, 100)]

    def test_plan_chunks_single_row(self):
        up = StreamingTextureUploader(chunk_height=1)
        chunks = up.plan_chunks(10, 3)
        assert len(chunks) == 3
        assert chunks == [(0, 1), (1, 1), (2, 1)]

    def test_upload_calls_fn(self):
        uploaded: list[tuple[int, TextureChunk]] = []

        def _upload(tex_id: int, chunk: TextureChunk):
            uploaded.append((tex_id, chunk))

        up = StreamingTextureUploader(chunk_height=100, upload_fn=_upload)
        count = up.upload(
            texture_id=42,
            width=200,
            height=250,
            get_chunk_data=lambda y, h, w: b"\x00" * (w * h * 4),
        )
        assert count == 3
        assert len(uploaded) == 3
        assert uploaded[0][0] == 42
        assert uploaded[0][1].y_offset == 0
        assert uploaded[0][1].height == 100
        assert uploaded[1][1].y_offset == 100
        assert uploaded[2][1].y_offset == 200
        assert uploaded[2][1].height == 50

    def test_upload_without_fn_raises(self):
        up = StreamingTextureUploader(chunk_height=100)
        with pytest.raises(RuntimeError, match="No upload_fn"):
            up.upload(1, 100, 100, lambda y, h, w: b"")

    def test_chunk_height_property(self):
        up = StreamingTextureUploader(chunk_height=512)
        assert up.chunk_height == 512


# ======================================================================
# FBOPool
# ======================================================================

class TestFBOPool:
    @staticmethod
    def _counter_factory():
        counter = {"n": 0}

        def create(w, h):
            counter["n"] += 1
            return f"fbo_{counter['n']}_{w}x{h}"

        destroyed = []

        def destroy(fbo_id):
            destroyed.append(fbo_id)

        return create, destroy, destroyed

    def test_acquire_creates_fbo(self):
        create, destroy, _ = self._counter_factory()
        pool = FBOPool(max_size=4, create_fn=create, destroy_fn=destroy)
        fbo = pool.acquire(800, 600)
        assert fbo == "fbo_1_800x600"
        assert pool.size == 1

    def test_acquire_reuses_cached(self):
        create, destroy, _ = self._counter_factory()
        pool = FBOPool(max_size=4, create_fn=create, destroy_fn=destroy)
        fbo1 = pool.acquire(800, 600)
        fbo2 = pool.acquire(800, 600)
        assert fbo1 == fbo2
        assert pool.size == 1

    def test_acquire_different_sizes(self):
        create, destroy, _ = self._counter_factory()
        pool = FBOPool(max_size=4, create_fn=create, destroy_fn=destroy)
        pool.acquire(800, 600)
        pool.acquire(1920, 1080)
        assert pool.size == 2

    def test_eviction_on_max_size(self):
        create, destroy, destroyed = self._counter_factory()
        pool = FBOPool(max_size=2, create_fn=create, destroy_fn=destroy)
        pool.acquire(100, 100)
        pool.acquire(200, 200)
        pool.acquire(300, 300)  # should evict 100x100
        assert pool.size == 2
        assert len(destroyed) == 1
        assert "100x100" in destroyed[0]

    def test_lru_eviction_order(self):
        create, destroy, destroyed = self._counter_factory()
        pool = FBOPool(max_size=2, create_fn=create, destroy_fn=destroy)
        pool.acquire(100, 100)
        pool.acquire(200, 200)
        # Touch 100x100 to make it recently used
        pool.acquire(100, 100)
        pool.acquire(300, 300)  # should evict 200x200 (LRU)
        assert not pool.contains(200, 200)
        assert pool.contains(100, 100)
        assert pool.contains(300, 300)

    def test_clear(self):
        create, destroy, destroyed = self._counter_factory()
        pool = FBOPool(max_size=4, create_fn=create, destroy_fn=destroy)
        pool.acquire(100, 100)
        pool.acquire(200, 200)
        pool.clear()
        assert pool.size == 0
        assert len(destroyed) == 2

    def test_contains(self):
        create, destroy, _ = self._counter_factory()
        pool = FBOPool(max_size=4, create_fn=create, destroy_fn=destroy)
        pool.acquire(800, 600)
        assert pool.contains(800, 600) is True
        assert pool.contains(1920, 1080) is False

    def test_max_size_property(self):
        pool = FBOPool(max_size=8)
        assert pool.max_size == 8

    def test_acquire_without_create_fn_raises(self):
        pool = FBOPool(max_size=4)
        with pytest.raises(RuntimeError, match="No create_fn"):
            pool.acquire(100, 100)

    def test_release_is_noop(self):
        create, destroy, _ = self._counter_factory()
        pool = FBOPool(max_size=4, create_fn=create, destroy_fn=destroy)
        pool.acquire(100, 100)
        pool.release(100, 100)  # should not raise, FBO stays cached
        assert pool.size == 1
