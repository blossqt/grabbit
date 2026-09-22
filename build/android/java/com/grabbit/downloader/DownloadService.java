package com.grabbit.downloader;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

/**
 * Keeps Grabbit's downloads going while it is not on screen.
 *
 * Android freezes an app soon after it leaves the screen unless it runs a
 * foreground service, and aria2 - a child process of the app - is frozen with
 * it. This is that service. It runs in the app's own process and does no work
 * of its own: being in the foreground is what keeps the process, and so the
 * downloads, going, and its wake lock keeps them going with the screen off.
 * Its notification is Android's own progress notification.
 *
 * Python drives it (android/grabbit_mobile/background.py): show() starts it,
 * or updates its notification once it runs, and hide() stops it.
 */
public class DownloadService extends Service {
    private static final String TAG = "GrabbitDownloads";
    private static final String CHANNEL = "downloads";
    private static final int NOTIFICATION = 1;
    // Android 15 allows a data-sync service six hours a day, so the wake lock
    // never needs to outlive that.
    private static final long LONGEST = 6 * 60 * 60 * 1000L;

    // Guards the notification: an update must never land after the service
    // has gone, or it would stay in the shade with nothing behind it.
    private static final Object LOCK = new Object();
    private static volatile boolean running = false;

    private PowerManager.WakeLock wakeLock;

    /**
     * Show this in the notification, starting the service if it is not
     * running. Android starts one only while the app is on screen; off
     * screen, the start throws IllegalStateException for the caller to catch.
     * percent is 0-100, or -1 when the size is not known yet.
     */
    public static void show(Context context, String title, String text, int percent) {
        synchronized (LOCK) {
            if (running) {
                manager(context).notify(NOTIFICATION, build(context, title, text, percent));
                return;
            }
        }
        context.startService(new Intent(context, DownloadService.class)
                .putExtra("title", title)
                .putExtra("text", text)
                .putExtra("percent", percent));
    }

    /** Stop it, and its notification with it. */
    public static void hide(Context context) {
        context.stopService(new Intent(context, DownloadService.class));
    }

    /** Whether it is running in the foreground. */
    public static boolean isRunning() {
        return running;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        String title = intent == null ? null : intent.getStringExtra("title");
        String text = intent == null ? null : intent.getStringExtra("text");
        int percent = intent == null ? -1 : intent.getIntExtra("percent", -1);
        Notification notification = build(this, title, text, percent);
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                startForeground(NOTIFICATION, notification,
                                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else {
                startForeground(NOTIFICATION, notification);
            }
        } catch (RuntimeException e) {
            // Refused: the app left the screen first, or has used up Android
            // 15's six hours for today. The downloads carry on while it is open.
            Log.w(TAG, "could not keep downloading in the background", e);
            stopSelf();
            return START_NOT_STICKY;
        }
        synchronized (LOCK) {
            running = true;
        }
        if (wakeLock == null) {
            PowerManager power = (PowerManager) getSystemService(POWER_SERVICE);
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "grabbit:downloads");
            wakeLock.setReferenceCounted(false);
            wakeLock.acquire(LONGEST);
        }
        return START_NOT_STICKY;
    }

    @Override
    public void onTimeout(int startId, int fgsType) {
        // Android 15's six hours for today are up: stop, as it requires.
        stopSelf();
    }

    @Override
    public void onDestroy() {
        synchronized (LOCK) {
            running = false;
            manager(this).cancel(NOTIFICATION);
        }
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        wakeLock = null;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private static NotificationManager manager(Context context) {
        return (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
    }

    private static Notification build(Context context, String title, String text, int percent) {
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
        builder.setSmallIcon(android.R.drawable.stat_sys_download)
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
        } else {
            builder.setProgress(0, 0, true);
        }
        return builder.build();
    }
}
