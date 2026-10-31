package dev.scanmap.capture

import android.annotation.SuppressLint
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log

/**
 * Two Wi-Fi sources:
 *  - the connected network's RSSI (not throttled), polled every [connPollMs] and also on
 *    every capabilities change;
 *  - full scans of every access point via startScan(). Android allows 4 scans per 2 minutes
 *    unless "Wi-Fi scan throttling" is turned off in Developer options; on a Pixel 10 with it
 *    off a full 2.4/5/6 GHz sweep completes about every 3-4 s.
 */
class WifiSampler(
    private val context: Context,
    private val onSample: (RadioSample) -> Unit,
    private val connPollMs: Long = 500,
) {
    private val wifi = context.applicationContext.getSystemService(WifiManager::class.java)
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private val handler = Handler(Looper.getMainLooper())
    @Volatile private var lastInfo: WifiInfo? = null
    private var running = false

    var scansRequested = 0; private set
    var scansThrottled = 0; private set
    var scansReceived = 0; private set
    val throttlingEnabled: Boolean get() = wifi.isScanThrottleEnabled

    private val netCallback = object : ConnectivityManager.NetworkCallback(ConnectivityManager.NetworkCallback.FLAG_INCLUDE_LOCATION_INFO) {
        override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
            val info = caps.transportInfo as? WifiInfo ?: return
            lastInfo = info
            emitConnected(info)
        }
        override fun onLost(network: Network) { lastInfo = null }
    }

    private val scanReceiver = object : BroadcastReceiver() {
        @SuppressLint("MissingPermission")
        override fun onReceive(c: Context, intent: Intent) {
            if (!intent.getBooleanExtra(WifiManager.EXTRA_RESULTS_UPDATED, false)) return
            scansReceived++
            for (r in wifi.scanResults) {
                onSample(
                    RadioSample(
                        // ScanResult.timestamp: microseconds since boot, when this AP was last seen.
                        tNs = r.timestamp * 1000L,
                        kind = SampleKind.WIFI_SCAN,
                        id = r.BSSID ?: "",
                        name = r.wifiSsid?.toString()?.trim('"') ?: "",
                        freqMhz = r.frequency,
                        rssiDbm = r.level,
                        channelWidthMhz = channelWidthMhz(r.channelWidth),
                        wifiStandard = r.wifiStandard,
                    )
                )
            }
            if (running) handler.postDelayed(::requestScan, 200)
        }
    }

    private val pollConnected = object : Runnable {
        @Suppress("DEPRECATION")
        override fun run() {
            if (!running) return
            // getConnectionInfo is deprecated but still returns a fresh RSSI; the callback's
            // WifiInfo is a snapshot that only updates on threshold crossings.
            val info = wifi.connectionInfo ?: lastInfo
            if (info != null && info.networkId != -1) emitConnected(info)
            handler.postDelayed(this, connPollMs)
        }
    }

    private fun emitConnected(info: WifiInfo) {
        val rssi = info.rssi
        if (rssi <= -127 || rssi >= 0) return
        val bssid = info.bssid ?: lastInfo?.bssid ?: ""
        if (bssid.isEmpty() || bssid == "02:00:00:00:00:00") return
        onSample(
            RadioSample(
                tNs = SystemClock.elapsedRealtimeNanos(),
                kind = SampleKind.WIFI_CONN,
                id = bssid,
                name = (info.ssid ?: lastInfo?.ssid ?: "").trim('"'),
                freqMhz = info.frequency,
                rssiDbm = rssi,
                wifiStandard = info.wifiStandard,
            )
        )
    }

    @Suppress("DEPRECATION")
    private fun requestScan() {
        if (!running) return
        scansRequested++
        if (!wifi.startScan()) {
            scansThrottled++
            // Throttled: try again later; connected-network RSSI keeps flowing meanwhile.
            handler.postDelayed(::requestScan, 5_000)
        }
    }

    fun start() {
        if (running) return
        running = true
        context.registerReceiver(scanReceiver, IntentFilter(WifiManager.SCAN_RESULTS_AVAILABLE_ACTION))
        try {
            cm.registerNetworkCallback(
                NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(),
                netCallback,
            )
        } catch (e: SecurityException) {
            Log.w(TAG, "network callback", e)
        }
        handler.post(pollConnected)
        requestScan()
    }

    fun stop() {
        if (!running) return
        running = false
        handler.removeCallbacksAndMessages(null)
        runCatching { context.unregisterReceiver(scanReceiver) }
        runCatching { cm.unregisterNetworkCallback(netCallback) }
    }

    companion object {
        private const val TAG = "WifiSampler"
        fun channelWidthMhz(w: Int): Int = when (w) {
            0 -> 20; 1 -> 40; 2 -> 80; 3 -> 160; 4 -> 160 /* 80+80 */; 5 -> 320
            else -> 0
        }
    }
}
