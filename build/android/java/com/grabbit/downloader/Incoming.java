package com.grabbit.downloader;

import android.app.Activity;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.util.Locale;

/**
 * A torrent file another app hands Grabbit: tapped in Files or Downloads, or
 * sent from a share sheet. Android hands over an address (content://) that
 * Grabbit may read while it holds the intent, not a path; this copies the
 * file out of it into the app's cache, where the link reader (analyze.py)
 * opens it as it would any torrent on disk.
 *
 * torrent is what the window calls (grabbit_mobile/bootstrap.py); the intent
 * filters that bring the file here are in intent_filters.xml.
 */
public class Incoming {
    private static final String TAG = "GrabbitIncoming";
    // Far more than any torrent: what is bigger than this is not one.
    private static final long LIMIT = 32L << 20;

    /** The path of the copy, or "" when the intent hands over no file. */
    public static String torrent(Activity activity, Intent intent) {
        if (intent == null) {
            return "";
        }
        Uri uri = null;
        if (Intent.ACTION_VIEW.equals(intent.getAction())) {
            uri = intent.getData();
        } else if (Intent.ACTION_SEND.equals(intent.getAction())) {
            uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        }
        if (uri == null) {
            return "";
        }
        String scheme = uri.getScheme();
        if (!"content".equals(scheme) && !"file".equals(scheme)) {
            return "";              // a magnet link, read as a link
        }
        File folder = new File(activity.getCacheDir(), "received");
        folder.mkdirs();
        File copy = new File(folder, nameOf(activity, uri));
        try (InputStream in = activity.getContentResolver().openInputStream(uri);
             OutputStream out = new FileOutputStream(copy)) {
            if (in == null) {
                return "";
            }
            byte[] buffer = new byte[1 << 16];
            long total = 0;
            int count;
            while ((count = in.read(buffer)) > 0) {
                total += count;
                if (total > LIMIT) {
                    throw new IOException("far too big for a torrent");
                }
                out.write(buffer, 0, count);
            }
            return copy.getAbsolutePath();
        } catch (IOException | RuntimeException e) {
            Log.w(TAG, "could not read the file handed over", e);
            copy.delete();
            return "";
        }
    }

    /** Its name as the app handing it over gives it, safe for a file name,
     * ending in .torrent - which is what the link reader goes by. */
    private static String nameOf(Activity activity, Uri uri) {
        String name = null;
        if ("content".equals(uri.getScheme())) {
            try (Cursor cursor = activity.getContentResolver().query(
                    uri, new String[]{OpenableColumns.DISPLAY_NAME}, null, null, null)) {
                if (cursor != null && cursor.moveToFirst()) {
                    name = cursor.getString(0);
                }
            } catch (RuntimeException e) {
                Log.w(TAG, "the file handed over has no name", e);
            }
        }
        if (name == null || name.isEmpty()) {
            name = uri.getLastPathSegment();
        }
        if (name == null || name.isEmpty()) {
            name = "received";
        }
        name = name.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]", "_");
        if (!name.toLowerCase(Locale.ROOT).endsWith(".torrent")) {
            name += ".torrent";
        }
        return name;
    }
}
