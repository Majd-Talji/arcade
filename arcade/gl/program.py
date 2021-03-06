from ctypes import (
    c_char, c_int, c_buffer,
    c_char_p,
    cast, POINTER, pointer, byref,
    create_string_buffer,
)
from typing import Dict, Iterable, Tuple, List, TYPE_CHECKING
import weakref

from pyglet import gl

from .uniform import Uniform
from .types import AttribFormat, GLTypes, SHADER_TYPE_NAMES
from .exceptions import ShaderException

if TYPE_CHECKING:  # handle import cycle caused by type hinting
    from arcade.gl import Context


class Program:
    """Compiled and linked shader program.

    Currently supports vertex, fragment and geometry shaders.
    Transform feedback also supported when output attributes
    names are passed in the varyings parameter.

    Access Uniforms via the [] operator.
    Example:
        program['MyUniform'] = value
    """
    __slots__ = (
        '_ctx', '_glo', '_uniforms', '_out_attributes', '_geometry_info',
        '_attributes', 'attribute_key', '__weakref__'
    )

    def __init__(self,
                 ctx,
                 *,
                 vertex_shader: str,
                 fragment_shader: str = None,
                 geometry_shader: str = None,
                 out_attributes: List[str] = None):
        """Create a Program.

        :param Context ctx: The context this program belongs to
        :param str vertex_shader: vertex shader source
        :param str fragment_shader: fragment shader source
        :param str geometry_shader: geometry shader source
        :param List[str] out_attributes: List of out attributes used in transform feedback.
        """
        self._ctx = ctx
        self._glo = glo = gl.glCreateProgram()
        self._out_attributes = out_attributes or []
        self._geometry_info = (0, 0, 0)
        self._attributes = []  # type: List[AttribFormat]
        self.attribute_key = "INVALID"  # type: str

        shaders = [(vertex_shader, gl.GL_VERTEX_SHADER)]
        if fragment_shader:
            shaders.append((fragment_shader, gl.GL_FRAGMENT_SHADER))
        if geometry_shader:
            shaders.append((geometry_shader, gl.GL_GEOMETRY_SHADER))

        shaders_id = []
        for shader_code, shader_type in shaders:
            shader = Program.compile_shader(shader_code, shader_type)
            gl.glAttachShader(self._glo, shader)
            shaders_id.append(shader)

        # For now we assume varyings can be set up if no fragment shader
        if not fragment_shader:
            self._setup_out_attributes()

        Program.link(self._glo)
        if geometry_shader:
            geometry_in = gl.GLint()
            geometry_out = gl.GLint()
            geometry_vertices = gl.GLint()
            gl.glGetProgramiv(self._glo, gl.GL_GEOMETRY_INPUT_TYPE, geometry_in)
            gl.glGetProgramiv(self._glo, gl.GL_GEOMETRY_OUTPUT_TYPE, geometry_out)
            gl.glGetProgramiv(self._glo, gl.GL_GEOMETRY_VERTICES_OUT, geometry_vertices)
            self._geometry_info = (geometry_in.value, geometry_out.value, geometry_vertices.value)

        # Flag shaders for deletion. Will only be deleted once detached from program.
        for shader in shaders_id:
            gl.glDeleteShader(shader)

        # Handle uniforms
        self._uniforms: Dict[str, Uniform] = {}
        self._introspect_attributes()
        self._introspect_uniforms()

        self.ctx.stats.incr('program')
        weakref.finalize(self, Program._delete, self.ctx, shaders_id, glo)

    @property
    def ctx(self) -> 'Context':
        """The context this program belongs to"""
        return self._ctx

    @property
    def glo(self) -> int:
        """The OpenGL resource id for this program"""
        return self._glo

    @property
    def attributes(self) -> Iterable[AttribFormat]:
        return self._attributes

    @property
    def out_attributes(self) -> List[str]:
        """Out attributes names used in transform feedback"""
        return self._out_attributes

    @property
    def geometry_input(self) -> int:
        """The geometry shader's input primitive type.
        This an be compared with ``GL_TRIANGLES``, ``GL_POINTS`` etc.
        """
        return self._geometry_info[0]

    @property
    def geometry_output(self) -> int:
        """The geometry shader's output primitive type.
        This an be compared with ``GL_TRIANGLES``, ``GL_POINTS`` etc.
        """
        return self._geometry_info[1]

    @property
    def geometry_vertices(self) -> int:
        """The maximum number of vertices that can be emitted"""
        return self._geometry_info[2]

    @staticmethod
    def _delete(ctx, shaders_id, prog_id):
        # Check to see if the context was already cleaned up from program
        # shut down. If so, we don't need to delete the shaders.
        if gl.current_context is None:
            return

        for shader_id in shaders_id:
            gl.glDetachShader(prog_id, shader_id)

        gl.glDeleteProgram(prog_id)

        ctx.stats.decr('program')

    def __getitem__(self, item):
        try:
            uniform = self._uniforms[item]
        except KeyError:
            raise ShaderException(f"Uniform with the name `{item}` was not found.")

        return uniform.getter()

    def __setitem__(self, key, value):
        # Ensure we are setting the uniform on this program
        if self._ctx.active_program != self:
            self.use()

        try:
            uniform = self._uniforms[key]
        except KeyError:
            raise ShaderException(f"Uniform with the name `{key}` was not found.")

        uniform.setter(value)

    def use(self):
        """Activates the shader"""
        # IMPORTANT: This is the only place glUseProgram should be called
        #            so we can track active program.
        if self._ctx.active_program != self:
            gl.glUseProgram(self._glo)
            self._ctx.active_program = self

    def _setup_out_attributes(self):
        """Set up transform feedback varyings"""
        if not self._out_attributes:
            return

        # Covert names to char**
        c_array = (c_char_p * len(self._out_attributes))()
        for i, name in enumerate(self._out_attributes):
            c_array[i] = name.encode()

        ptr = cast(c_array, POINTER(POINTER(c_char)))

        # NOTE: We only support interleaved attributes for now
        gl.glTransformFeedbackVaryings(
            self._glo,  # program
            len(self._out_attributes),  # number of varying variables used for transform feedback
            ptr,  # zero-terminated strings specifying the names of the varying variables
            gl.GL_INTERLEAVED_ATTRIBS,
        )

    def _introspect_attributes(self):
        """Introspect and store detailed info about an attribute"""
        # TODO: Ensure gl_* attributes are ignored
        num_attrs = gl.GLint()
        gl.glGetProgramiv(self._glo, gl.GL_ACTIVE_ATTRIBUTES, num_attrs)
        num_varyings = gl.GLint()
        gl.glGetProgramiv(self._glo, gl.GL_TRANSFORM_FEEDBACK_VARYINGS, num_varyings)
        # print(f"attrs {num_attrs.value} varyings={num_varyings.value}")

        for i in range(num_attrs.value):
            c_name = create_string_buffer(256)
            c_size = gl.GLint()
            c_type = gl.GLenum()
            gl.glGetActiveAttrib(
                self._glo,  # program to query
                i,  # index (not the same as location)
                256,  # max attr name size
                None,  # c_length,  # length of name
                c_size,  # size of attribute (array or not)
                c_type,  # attribute type (enum)
                c_name,  # name buffer
            )

            # Get the actual location. Do not trust the original order
            location = gl.glGetAttribLocation(self._glo, c_name)

            # print(c_name.value, c_size, c_type)
            type_info = GLTypes.get(c_type.value)
            # print(type_info)
            self._attributes.append(AttribFormat(
                c_name.value.decode(),
                type_info.gl_type,
                type_info.components,
                type_info.gl_size,
                location=location,
            ))

        # The attribute key is used to cache VertexArrays
        self.attribute_key = ':'.join(f'{attr.name}[{attr.gl_type}/{attr.components}]' for attr in self._attributes)

    def _introspect_uniforms(self):
        """Figure out what uniforms are available and build an internal map"""
        # Number of active uniforms in the program
        active_uniforms = gl.GLint(0)
        gl.glGetProgramiv(self._glo, gl.GL_ACTIVE_UNIFORMS, byref(active_uniforms))

        # Loop all the active uniforms
        for index in range(active_uniforms.value):
            # Query uniform information like name, type, size etc.
            u_name, u_type, u_size = self._query_uniform(index)
            u_location = gl.glGetUniformLocation(self._glo, u_name.encode())

            # Skip uniforms that may be in Uniform Blocks
            # TODO: We should handle all uniforms
            if u_location == -1:
                # print(f"Uniform {u_location} {u_name} {u_size} {u_type} skipped")
                continue

            u_name = u_name.replace('[0]', '')  # Remove array suffix
            self._uniforms[u_name] = Uniform(self._glo, u_location, u_name, u_type, u_size)

    def _query_uniform(self, location: int) -> Tuple[str, int, int]:
        """Retrieve Uniform information at given location.

        Returns the name, the type as a GLenum (GL_FLOAT, ...) and the size. Size is
        greater than 1 only for Uniform arrays, like an array of floats or an array
        of Matrices.
        """
        usize = gl.GLint()
        utype = gl.GLenum()
        buf_size = 192  # max uniform character length
        uname = create_string_buffer(buf_size)
        gl.glGetActiveUniform(
            self._glo,  # program to query
            location,  # location to query
            buf_size,  # size of the character/name buffer
            None,  # the number of characters actually written by OpenGL in the string
            usize,  # size of the uniform variable
            utype,  # data type of the uniform variable
            uname  # string buffer for storing the name
        )
        return uname.value.decode(), utype.value, usize.value

    @staticmethod
    def compile_shader(source: str, shader_type: gl.GLenum) -> gl.GLuint:
        """Compile the shader code of the given type.

        `shader_type` could be GL_VERTEX_SHADER, GL_FRAGMENT_SHADER, ...

        Returns the shader id as a GLuint
        """
        shader = gl.glCreateShader(shader_type)
        source_bytes = source.encode('utf-8')
        # Turn the source code string into an array of c_char_p arrays.
        strings = byref(
            cast(
                c_char_p(source_bytes),
                POINTER(c_char)
            )
        )
        # Make an array with the strings lengths
        lengths = pointer(c_int(len(source_bytes)))
        gl.glShaderSource(shader, 1, strings, lengths)
        gl.glCompileShader(shader)
        result = c_int()
        gl.glGetShaderiv(shader, gl.GL_COMPILE_STATUS, byref(result))
        if result.value == gl.GL_FALSE:
            msg = create_string_buffer(512)
            length = c_int()
            gl.glGetShaderInfoLog(shader, 512, byref(length), msg)
            raise ShaderException((
                f"Error compiling {SHADER_TYPE_NAMES[shader_type]} "
                f"({result.value}): {msg.value.decode('utf-8')}\n"
                f"---- [{SHADER_TYPE_NAMES[shader_type]}] ---\n"
            ) + '\n'.join(f"{str(i+1).zfill(3)}: {line} " for i, line in enumerate(source.split('\n'))))
        return shader

    @staticmethod
    def link(glo):
        gl.glLinkProgram(glo)
        status = c_int()
        gl.glGetProgramiv(glo, gl.GL_LINK_STATUS, status)
        if not status.value:
            length = c_int()
            gl.glGetProgramiv(glo, gl.GL_INFO_LOG_LENGTH, length)
            log = c_buffer(length.value)
            gl.glGetProgramInfoLog(glo, len(log), None, log)
            raise ShaderException('Program link error: {}'.format(log.value.decode()))

    def __repr__(self):
        return "<Program id={}>".format(self._glo)
