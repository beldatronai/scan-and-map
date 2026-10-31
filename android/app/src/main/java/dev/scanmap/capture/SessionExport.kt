package dev.scanmap.capture

import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

object SessionExport {
    /** Zip a session folder. JPEGs are stored (already compressed); CSV/PLY are deflated. */
    fun zip(sessionDir: File, outDir: File): File {
        outDir.mkdirs()
        val out = File(outDir, sessionDir.name + ".zip")
        ZipOutputStream(out.outputStream().buffered(1 shl 16)).use { zos ->
            sessionDir.walkTopDown().filter { it.isFile }.forEach { f ->
                val rel = sessionDir.name + "/" + f.relativeTo(sessionDir).invariantSeparatorsPath
                val entry = ZipEntry(rel)
                if (f.extension.lowercase() == "jpg") {
                    entry.method = ZipEntry.STORED
                    entry.size = f.length()
                    entry.compressedSize = f.length()
                    entry.crc = crc32(f)
                }
                zos.putNextEntry(entry)
                f.inputStream().use { it.copyTo(zos, 1 shl 16) }
                zos.closeEntry()
            }
        }
        return out
    }

    private fun crc32(f: File): Long {
        val crc = java.util.zip.CRC32()
        f.inputStream().use { input ->
            val buf = ByteArray(1 shl 16)
            while (true) { val n = input.read(buf); if (n < 0) break; crc.update(buf, 0, n) }
        }
        return crc.value
    }

    /**
     * Stream the zip to the Fedora receiver: POST {server}/api/sessions?name=...&process=1
     * with Content-Type application/zip. Returns the server's JSON reply.
     */
    fun upload(zip: File, server: String, process: Boolean, onProgress: (Long, Long) -> Unit): String {
        val base = server.trimEnd('/')
        val url = URL("$base/api/sessions?name=" + URLEncoder.encode(zip.nameWithoutExtension, "UTF-8") + "&process=" + (if (process) 1 else 0))
        val conn = url.openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 5_000
            conn.readTimeout = 120_000
            conn.setRequestProperty("Content-Type", "application/zip")
            conn.setFixedLengthStreamingMode(zip.length())
            val total = zip.length()
            var sent = 0L
            conn.outputStream.use { os ->
                zip.inputStream().use { input ->
                    val buf = ByteArray(1 shl 16)
                    while (true) {
                        val n = input.read(buf); if (n < 0) break
                        os.write(buf, 0, n); sent += n; onProgress(sent, total)
                    }
                }
            }
            val code = conn.responseCode
            val body = (if (code in 200..299) conn.inputStream else conn.errorStream)?.bufferedReader()?.readText() ?: ""
            if (code !in 200..299) error("HTTP $code: $body")
            return body
        } finally {
            conn.disconnect()
        }
    }
}
