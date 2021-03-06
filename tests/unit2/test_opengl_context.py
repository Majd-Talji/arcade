"""
Low level tests for OpenGL 3.3 wrappers.
"""
import array
import pytest
import arcade

from pyglet import gl

SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600


@pytest.fixture(scope="module")
def ctx():
    window = arcade.Window(SCREEN_WIDTH, SCREEN_HEIGHT, "Test OpenGL")
    yield window.ctx
    window.close()


def test_ctx(ctx):
    assert ctx.gl_version >= (3, 3)
    assert ctx.limits.MAX_TEXTURE_SIZE > 4096
    assert ctx.limits.MAX_ARRAY_TEXTURE_LAYERS >= 256

    assert ctx.blend_func == ctx.BLEND_DEFAULT
    ctx.blend_func = ctx.BLEND_PREMULTIPLIED_ALPHA
    assert ctx.blend_func == ctx.BLEND_PREMULTIPLIED_ALPHA


def test_enable_disable(ctx):
    # Blend is enabled by default
    assert ctx.is_enabled(ctx.BLEND)
    ctx.enable_only() 
    assert len(ctx._flags) == 0

    ctx.enable(ctx.BLEND)
    ctx.enable(ctx.BLEND, ctx.DEPTH_TEST, ctx.CULL_FACE)
    assert ctx.is_enabled(ctx.BLEND)
    assert ctx.is_enabled(ctx.DEPTH_TEST)
    assert ctx.is_enabled(ctx.CULL_FACE)

    ctx.disable(ctx.BLEND)
    assert ctx.is_enabled(ctx.BLEND) is False
    assert len(ctx._flags) == 2
