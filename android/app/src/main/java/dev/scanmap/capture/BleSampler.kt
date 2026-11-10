package dev.scanmap.capture

import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.util.Log

/** Continuous BLE advertisement scanning; every advertisement is one sample with its own timestamp. */
class BleSampler(context: Context, private val onSample: (RadioSample) -> Unit) {
    private val adapter = context.getSystemService(BluetoothManager::class.java)?.adapter
    private var running = false
    var advertisements = 0L; private set
    var lastError: Int = 0; private set

    private val callback = object : ScanCallback() {
        override fun onScanResult(callbackType: Int, result: ScanResult) = handle(result)
        override fun onBatchScanResults(results: MutableList<ScanResult>) = results.forEach(::handle)
        override fun onScanFailed(errorCode: Int) {
            lastError = errorCode
            Log.w("BleSampler", "scan failed $errorCode")
        }
    }

    @SuppressLint("MissingPermission")
    private fun handle(r: ScanResult) {
        advertisements++
        val name = r.scanRecord?.deviceName ?: runCatching { r.device.name }.getOrNull() ?: ""
        onSample(
            RadioSample(
                tNs = r.timestampNanos,
                kind = SampleKind.BLE,
                id = r.device.address,
                name = name,
                freqMhz = 2440,
                rssiDbm = r.rssi,
                txPowerDbm = if (r.txPower == ScanResult.TX_POWER_NOT_PRESENT) Int.MIN_VALUE else r.txPower,
            )
        )
    }

    @SuppressLint("MissingPermission")
    fun start() {
        val bt = adapter ?: return
        val scanner = bt.bluetoothLeScanner ?: return
        if (running) return
        val extended = bt.isLeExtendedAdvertisingSupported
        val builder = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .setCallbackType(ScanSettings.CALLBACK_TYPE_ALL_MATCHES)
            .setMatchMode(ScanSettings.MATCH_MODE_AGGRESSIVE)
            .setNumOfMatches(ScanSettings.MATCH_NUM_MAX_ADVERTISEMENT)
            .setReportDelay(0)
        if (extended) {
            // Pixel 10 supports BLE 5 extended advertising; scanning for it also catches newer beacons.
            builder.setLegacy(false).setPhy(ScanSettings.PHY_LE_ALL_SUPPORTED)
        }
        val settings = builder.build()
        scanner.startScan(null, settings, callback)
        running = true
    }

    @SuppressLint("MissingPermission")
    fun stop() {
        if (!running) return
        runCatching { adapter?.bluetoothLeScanner?.stopScan(callback) }
        running = false
    }
}
