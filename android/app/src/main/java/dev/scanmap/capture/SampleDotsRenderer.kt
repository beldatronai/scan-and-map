package dev.scanmap.capture

import android.opengl.GLES20
import java.nio.FloatBuffer

/**
 * Live coverage overlay: one dot per connected-Wi-Fi sample, coloured from red (-90 dBm)
 * to green (-30 dBm). Points are appended from any thread and uploaded on the GL thread.
 */
class SampleDotsRenderer(private val maxDots: Int = 20_000) {
    private var program = 0
    private var aPos = 0; private var aColor = 0; private var uMvp = 0; private var uSize = 0
    private val data: FloatBuffer = GlUtil.floatBuffer(maxDots * 7)
    private var count = 0
    private val lock = Any()

    fun create() {
        program = GlUtil.program(VS, FS)
        aPos = GLES20.glGetAttribLocation(program, "a_Position")
        aColor = GLES20.glGetAttribLocation(program, "a_Color")
        uMvp = GLES20.glGetUniformLocation(program, "u_Mvp")
        uSize = GLES20.glGetUniformLocation(program, "u_Size")
    }

    fun clear() { synchronized(lock) { count = 0 } }

    fun add(pos: FloatArray, rssi: Int) {
      synchronized(lock) {
        if (count >= maxDots) return
        val c = colorFor(rssi)
        data.position(count * 7)
        data.put(pos[0]); data.put(pos[1] - 0.25f); data.put(pos[2]) // a bit below the lens, near hand height
        data.put(c[0]); data.put(c[1]); data.put(c[2]); data.put(0.9f)
        count++
      }
    }

    fun draw(viewProj: FloatArray) {
      synchronized(lock) {
        if (count == 0) return
        GLES20.glUseProgram(program)
        GLES20.glUniformMatrix4fv(uMvp, 1, false, viewProj, 0)
        GLES20.glUniform1f(uSize, 18f)
        GLES20.glEnable(GLES20.GL_BLEND)
        GLES20.glBlendFunc(GLES20.GL_SRC_ALPHA, GLES20.GL_ONE_MINUS_SRC_ALPHA)
        data.position(0)
        GLES20.glVertexAttribPointer(aPos, 3, GLES20.GL_FLOAT, false, 28, data)
        data.position(3)
        GLES20.glVertexAttribPointer(aColor, 4, GLES20.GL_FLOAT, false, 28, data)
        GLES20.glEnableVertexAttribArray(aPos)
        GLES20.glEnableVertexAttribArray(aColor)
        GLES20.glDrawArrays(GLES20.GL_POINTS, 0, count)
        GLES20.glDisableVertexAttribArray(aPos)
        GLES20.glDisableVertexAttribArray(aColor)
        GLES20.glDisable(GLES20.GL_BLEND)
      }
    }

    companion object {
        fun colorFor(rssi: Int): FloatArray {
            val t = ((rssi + 90f) / 60f).coerceIn(0f, 1f) // 0 = -90 dBm, 1 = -30 dBm
            return if (t < 0.5f) floatArrayOf(1f, t * 2f, 0.1f) else floatArrayOf(2f - t * 2f, 1f, 0.1f)
        }

        private const val VS = """
            uniform mat4 u_Mvp;
            uniform float u_Size;
            attribute vec4 a_Position;
            attribute vec4 a_Color;
            varying vec4 v_Color;
            void main() {
                gl_Position = u_Mvp * vec4(a_Position.xyz, 1.0);
                gl_PointSize = u_Size / max(gl_Position.w, 0.3);
                v_Color = a_Color;
            }
        """
        private const val FS = """
            precision mediump float;
            varying vec4 v_Color;
            void main() {
                vec2 d = gl_PointCoord - vec2(0.5);
                if (dot(d, d) > 0.25) discard;
                gl_FragColor = v_Color;
            }
        """
    }
}
