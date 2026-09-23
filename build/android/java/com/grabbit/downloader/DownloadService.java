package com.grabbit.downloader;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Settings;
import android.util.Log;

import org.kivy.android.PythonService;

/**
 * Grabbit's downloader, in a process of its own.
 *
 * python-for-android makes ServiceEngine from this (build_apk.sh, --service):
 * a Python interpreter in the ":service_engine" process, running service.py,
 * which owns aria2 and every download. The window runs in the app's main
 * process and only talks to it (grabbit_mobile/remote.py). That split is the
 * point: closing the window - swiping Grabbit out of the recent apps
 * included - ends the main process, and the downloads would end with it if
 * they lived there.
 *
 * It is in the foreground whenever anything is downloading or seeding, which
 * keeps Android from freezing the process, and shows Android's own progress
 * notification; its wake lock keeps the transfers going with the screen off.
 * Python decides when (grabbit_mobile/background.py) through the static
 * methods below.
 *
 * Nothing here may throw into Android: an exception in a service takes the
 * whole process down, and the downloads with it.
 */
public class DownloadService extends PythonService {
    private static final String TAG = "GrabbitDownloads";
    private static final String CHANNEL = "downloads";
    private static final int NOTIFICATION = 1;

    /** Sent by the window as a download is asked for: into the foreground at once. */
    public static final String ACTION_FOREGROUND = "com.grabbit.downloader.FOREGROUND";
    // After that, how long to stay there with nothing yet to show for it: the
    // download reaches the engine a moment after the tap.
    private static final long HOLD = 15_000;

    private static final Object LOCK = new Object();
    private static DownloadService instance;
    private static boolean foreground;
    private static long holdUntil;

    private PowerManager.WakeLock wakeLock;

    @Override
    public int startType() {
        // Sticky, for two reasons. PythonService stops a service that is not
        // when its task is removed - when Grabbit is swiped away - which is
        // exactly what this process exists to survive. And if Android ends
        // the process to free memory, it starts it again, and the engine
        // picks the downloads up where they were.
        return START_STICKY;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        synchronized (LOCK) {
            instance = this;
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        try {
            if (intent != null && ACTION_FOREGROUND.equals(intent.getAction())) {
                synchronized (LOCK) {
                    holdUntil = SystemClock.elapsedRealtime() + HOLD;
                    if (!foreground) {
                        enterForeground(intent.getStringExtra("title"), "Starting", -1, false);
                    }
                }
            }
        } catch (RuntimeException e) {
            // Refused: the app left the screen first. The download still
            // runs; it just may not keep running once Grabbit is left.
            Log.w(TAG, "could not come to the foreground", e);
        }
        try {
            // Starts the Python side, the first time.
            return super.onStartCommand(intent, flags, startId);
        } catch (RuntimeException e) {
            Log.e(TAG, "could not start the downloader", e);
            stopSelf();
            return START_NOT_STICKY;
        }
    }

    @Override
    public void onTaskRemoved(Intent rootIntent) {
        // Swiped away: the window is gone, the downloads are not.
        Log.i(TAG, "Grabbit was closed; the downloads carry on");
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public void onTimeout(int startId, int fgsType) {
        // Only the time-limited service types get this, and this one is not
        // of them - but if Android ever says time is up, it must be obeyed.
        synchronized (LOCK) {
            if (foreground) {
                leaveForeground();
            }
        }
    }

    @Override
    public void onDestroy() {
        synchronized (LOCK) {
            foreground = false;
            instance = null;
            try {
                manager(this).cancel(NOTIFICATION);
            } catch (RuntimeException e) {
                Log.w(TAG, "could not clear the notification", e);
            }
        }
        releaseWakeLock();
        super.onDestroy();          // PythonService ends the process here
    }

    // ------------------------------------------------ from the window

    /** Start the downloader, if it is not running. Android allows this only
     * while the app is on screen; otherwise it throws, for the caller. */
    public static void begin(Context context) {
        context.startService(defaultIntent(context));
    }

    /** A download was just asked for, from a tap: start the downloader if need
     * be, straight into the foreground, while the app is certainly on screen -
     * the only time Android allows it. */
    public static void expect(Context context, String title) {
        context.startService(defaultIntent(context)
                .setAction(ACTION_FOREGROUND)
                .putExtra("title", title));
    }

    /** Whether Android lets Grabbit run with no battery limits. */
    public static boolean unrestricted(Context context) {
        try {
            if (Build.VERSION.SDK_INT < 23) {
                return true;
            }
            PowerManager power = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
            return power.isIgnoringBatteryOptimizations(context.getPackageName());
        } catch (RuntimeException e) {
            return false;
        }
    }

    /** Android's own question: let Grabbit run with no battery limits? The
     * person answers it; the app only asks. False where it cannot be asked. */
    public static boolean askForUnrestricted(Context context) {
        if (Build.VERSION.SDK_INT < 23) {
            return false;
        }
        try {
            Intent ask = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                                    Uri.parse("package:" + context.getPackageName()));
            context.startActivity(ask);
            return true;
        } catch (RuntimeException e) {
            try {
                context.startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                return true;
            } catch (RuntimeException ignored) {
                return false;
            }
        }
    }

    private static Intent defaultIntent(Context context) {
        // ServiceEngine is generated by python-for-android, and so is the
        // intent that starts its Python: ask it for one rather than copy it.
        try {
            Class<?> engine = Class.forName(context.getPackageName() + ".ServiceEngine");
            return (Intent) engine.getMethod("getDefaultIntent", Context.class, String.class,
                                             String.class, String.class, String.class)
                                  .invoke(null, context, "", "", "", "");
        } catch (ReflectiveOperationException e) {
            throw new IllegalStateException("this build has no ServiceEngine", e);
        }
    }

    // ---------------------------------------------- from the downloader

    /**
     * Show this in the notification, coming into the foreground first if need
     * be. percent is 0-100, -1 while the size is unknown, and -2 for no bar at
     * all (seeding). False when Android will not have it: out of the
     * foreground, a service cannot come back into it while the app is off
     * screen.
     */
    public static boolean show(String title, String text, int percent, boolean seeding) {
        synchronized (LOCK) {
            DownloadService service = instance;
            if (service == null) {
                return false;
            }
            try {
                if (foreground) {
                    manager(service).notify(NOTIFICATION, build(service, title, text, percent, seeding));
                } else {
                    service.enterForeground(title, text, percent, seeding);
                }
                return true;
            } catch (RuntimeException e) {
                Log.w(TAG, "could not keep downloading in the background", e);
                return false;
            }
        }
    }

    /** Leave the foreground, and the notification with it - unless a download
     * was asked for a moment ago and has not reached the engine yet. True once
     * out of it. */
    public static boolean hide() {
        synchronized (LOCK) {
            if (!foreground || instance == null) {
                return true;
            }
            if (SystemClock.elapsedRealtime() < holdUntil) {
                return false;
            }
            try {
                instance.leaveForeground();
            } catch (RuntimeException e) {
                Log.w(TAG, "could not leave the foreground", e);
            }
            return !foreground;
        }
    }

    public static boolean isForeground() {
        synchronized (LOCK) {
            return foreground;
        }
    }

    /** The downloader is done - nothing to fetch or seed, and the window is
     * not asking for anything - so end the service, and its process. */
    public static void finish() {
        synchronized (LOCK) {
            if (instance != null) {
                instance.stopSelf();
            }
        }
    }

    /** Whether there is a network to download over at all. */
    public static boolean online() {
        return network() != NO_NETWORK;
    }

    /** Whether the network in use costs nothing by the byte - Wi-Fi, as a rule. */
    public static boolean unmetered() {
        int network = network();
        return network == FREE || network == UNKNOWN;
    }

    // What network() finds. UNKNOWN is "could not ask", and counts as a network
    // that is there and free, so failing to ask never holds a download back.
    private static final int UNKNOWN = -1, NO_NETWORK = 0, METERED = 1, FREE = 2;

    private static int network() {
        DownloadService service = instance;
        if (service == null || Build.VERSION.SDK_INT < 23) {
            return UNKNOWN;
        }
        try {
            ConnectivityManager connectivity =
                    (ConnectivityManager) service.getSystemService(CONNECTIVITY_SERVICE);
            Network network = connectivity.getActiveNetwork();
            NetworkCapabilities capabilities =
                    network == null ? null : connectivity.getNetworkCapabilities(network);
            if (capabilities == null
                    || !capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) {
                return NO_NETWORK;
            }
            return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED)
                    ? FREE : METERED;
        } catch (RuntimeException e) {
            return UNKNOWN;
        }
    }

    // ------------------------------------------------------------ inside

    private void enterForeground(String title, String text, int percent, boolean seeding) {
        Notification notification = build(this, title, text, percent, seeding);
        if (Build.VERSION.SDK_INT >= 34) {
            // specialUse has no daily limit. dataSync, the obvious type, gets
            // six hours a day from Android 15 on, and then must stop - in the
            // middle of a large torrent, or of seeding one.
            startForeground(NOTIFICATION, notification,
                            ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(NOTIFICATION, notification);
        }
        foreground = true;
        holdWakeLock();
    }

    private void leaveForeground() {
        if (Build.VERSION.SDK_INT >= 24) {
            stopForeground(STOP_FOREGROUND_REMOVE);
        } else {
            stopForeground(true);
        }
        foreground = false;
        releaseWakeLock();
    }

    private void holdWakeLock() {
        try {
            if (wakeLock == null) {
                PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
                wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "grabbit:downloads");
                wakeLock.setReferenceCounted(false);
            }
            // For as long as the foreground lasts - which is as long as there
            // is anything to fetch or seed, and no longer.
            if (!wakeLock.isHeld()) {
                wakeLock.acquire();
            }
        } catch (RuntimeException e) {
            // Downloads still carry on off screen, if not with the screen off.
            Log.w(TAG, "could not hold a wake lock", e);
        }
    }

    private void releaseWakeLock() {
        try {
            if (wakeLock != null && wakeLock.isHeld()) {
                wakeLock.release();
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "could not release the wake lock", e);
        }
    }

    private static NotificationManager manager(Context context) {
        return (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
    }

    private static Notification build(Context context, String title, String text,
                                       int percent, boolean seeding) {
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager manager = manager(context);
            if (manager.getNotificationChannel(CHANNEL) == null) {
                NotificationChannel channel = new NotificationChannel(
                        CHANNEL, "Downloads", NotificationManager.IMPORTANCE_LOW);
                channel.setDescription("Progress of the downloads that are running");
                manager.createNotificationChannel(channel);
            }
            builder = new Notification.Builder(context, CHANNEL);
        } else {
            builder = new Notification.Builder(context);
        }
        builder.setSmallIcon(seeding ? android.R.drawable.stat_sys_upload
                                     : android.R.drawable.stat_sys_download)
               .setContentTitle(title == null ? "Downloading" : title)
               .setContentText(text)
               .setCategory(Notification.CATEGORY_PROGRESS)
               .setOngoing(true)
               .setOnlyAlertOnce(true)
               .setShowWhen(false);
        // Touching it brings Grabbit back, as it was.
        Intent open = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        if (open != null) {
            builder.setContentIntent(PendingIntent.getActivity(
                    context, 0, open, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        }
        if (percent >= 0) {
            builder.setProgress(100, percent, false);
        } else if (percent == -1) {
            builder.setProgress(0, 0, true);
        }
        return builder.build();
    }
}
