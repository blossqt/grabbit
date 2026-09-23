package com.grabbit.downloader;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * After the phone restarts, starts the downloader again - if anything was
 * still downloading or seeding when it went down. Phones restart more often
 * than people think: Samsung's own does, overnight, to keep itself quick.
 *
 * It reads the download list the downloader keeps (files/grabbit/tasks.json)
 * and does nothing if there is nothing to go on with, so a restart costs a
 * phone with nothing unfinished not even a notification. build_apk.sh
 * declares it in the manifest, since python-for-android has no switch for a
 * receiver.
 */
public class RestartReceiver extends BroadcastReceiver {
    private static final String TAG = "GrabbitDownloads";

    // Everything that is on its way or giving back: what a restart interrupted.
    // Paused, finished and failed downloads stay as they were left.
    private static final Set<String> UNFINISHED = new HashSet<>(Arrays.asList(
            "queued", "metadata", "extracting", "downloading", "processing", "checking",
            "seeding"));

    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            return;
        }
        try {
            int count = unfinished(context);
            if (count == 0) {
                return;
            }
            if (!unpacked(context)) {
                // Updated and not opened since: the downloader's Python is still
                // the old version's, which the new libraries may not run. The
                // downloads go on as soon as Grabbit is opened.
                Log.i(TAG, "not resuming after the restart: Grabbit has not been opened since it was updated");
                return;
            }
            Log.i(TAG, "resuming " + count + " download(s) after the phone restarted");
            DownloadService.resume(context, count == 1 ? "Resuming a download" : "Resuming " + count + " downloads");
        } catch (RuntimeException e) {
            // Never a crash for a phone that has just started; the downloads
            // wait for Grabbit to be opened instead.
            Log.w(TAG, "could not resume the downloads after the restart", e);
        }
    }

    /** How many downloads were on their way when the phone went down. */
    static int unfinished(Context context) {
        File list = new File(context.getFilesDir(), "grabbit/tasks.json");
        if (!list.isFile()) {
            return 0;
        }
        try (InputStream in = new FileInputStream(list)) {
            byte[] data = new byte[(int) list.length()];
            int read = 0;
            while (read < data.length) {
                int n = in.read(data, read, data.length - read);
                if (n < 0) {
                    break;
                }
                read += n;
            }
            JSONArray tasks = new JSONObject(new String(data, 0, read, StandardCharsets.UTF_8))
                    .optJSONArray("tasks");
            int count = 0;
            for (int i = 0; tasks != null && i < tasks.length(); i++) {
                JSONObject task = tasks.optJSONObject(i);
                if (task != null && UNFINISHED.contains(task.optString("state"))) {
                    count++;
                }
            }
            return count;
        } catch (IOException | org.json.JSONException e) {
            Log.w(TAG, "could not read the download list", e);
            return 0;
        }
    }

    /** Whether the app's Python files on disk are this version's: the window
     * unpacks them, the first time it is opened after an update. */
    static boolean unpacked(Context context) {
        int id = context.getResources().getIdentifier(
                "private_version", "string", context.getPackageName());
        if (id == 0) {
            return true;                    // nothing to compare: assume they are
        }
        String wanted = context.getString(id);
        File marker = new File(context.getFilesDir(), "app/private.version");
        try (InputStream in = new FileInputStream(marker)) {
            byte[] data = new byte[(int) Math.min(marker.length(), 256)];
            int read = in.read(data);
            return read > 0 && new String(data, 0, read, StandardCharsets.UTF_8).trim().equals(wanted.trim());
        } catch (IOException e) {
            return false;
        }
    }
}
