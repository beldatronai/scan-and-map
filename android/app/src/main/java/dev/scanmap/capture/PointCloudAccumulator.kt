package dev.scanmap.capture

import com.google.ar.core.PointCloud
import java.io.File

/**
 * Merges ARCore feature points from every frame into a sparse voxel set (default 4 cm),
 * written at the end as depth_points.ply: a cheap fallback model when photogrammetry fails.
 */
class PointCloudAccumulator(private val voxel: Float = 0.04f, private val maxPoints: Int = 400_000) {
    private val points = HashMap<Long, FloatArray>(1 shl 16) // key -> x,y,z,confidence,count

    val size: Int get() = synchronized(points) { points.size }

    fun add(cloud: PointCloud, minConfidence: Float = 0.3f) {
        val buf = cloud.points // x, y, z, confidence
        synchronized(points) {
            while (buf.remaining() >= 4) {
                val x = buf.get(); val y = buf.get(); val z = buf.get(); val c = buf.get()
                if (c < minConfidence) continue
                val k = key(x, y, z)
                val p = points[k]
                if (p == null) {
                    if (points.size < maxPoints) points[k] = floatArrayOf(x, y, z, c, 1f)
                } else {
                    val n = p[4] + 1f
                    p[0] += (x - p[0]) / n; p[1] += (y - p[1]) / n; p[2] += (z - p[2]) / n
                    p[3] = maxOf(p[3], c); p[4] = n
                }
            }
        }
    }

    private fun key(x: Float, y: Float, z: Float): Long {
        val ix = (x / voxel).toInt().toLong() and 0x1FFFFF
        val iy = (y / voxel).toInt().toLong() and 0x1FFFFF
        val iz = (z / voxel).toInt().toLong() and 0x1FFFFF
        return (ix shl 42) or (iy shl 21) or iz
    }

    fun writePly(file: File) {
        val snapshot = synchronized(points) { points.values.filter { it[4] >= 2f }.map { it.copyOf() } }
        file.bufferedWriter().use { w ->
            w.write("ply\nformat ascii 1.0\nelement vertex ${snapshot.size}\n")
            w.write("property float x\nproperty float y\nproperty float z\nproperty float confidence\nproperty uchar observations\nend_header\n")
            for (p in snapshot) w.write("${p[0]} ${p[1]} ${p[2]} ${p[3]} ${p[4].toInt().coerceAtMost(255)}\n")
        }
    }
}
