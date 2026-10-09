package com.grabbit.downloader;

import android.app.Activity;
import android.content.ContentResolver;
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
 * A .torrent another app hands over: tapped in a file manager, a browser's
 * downloads or a chat, or sent from one through the share sheet
 * (intent_filters.xml is what makes Android offer Grabbit for them).
 *
 * What arrives is not a path but an address the other app answers for, with
 * leave to read it that lasts only as long as this activity does. So the file
 * is copied in at once, under its own name, and the window reads the copy as
 * the desktop reads a .torrent's path (grabbit/analyze.py).
 *
 * torrent is what the window calls (grabbit_mobile/bootstrap.py).
 */
public class Incoming {
    private static final String TAG = "GrabbitIncoming";

    // What torrent answers when there was a file but it could not be read.
    public static final String FAILED = "failed";

    // A .torrent holds names and hashes - kilobytes, a few megabytes for the
    // largest. Anything past this is not one (analyze.MAX_TORRENT_BYTES).
    private static final long MOST = 8L * 1024 * 1024;

    /** Copy the file an intent carries into folder: the copy's path, "" when
     * the intent carries no file, or FAILED. */
    public static String torrent(Activity activity, Intent intent, String folder) {
        Uri uri;
        try {
            uri = address(intent);
        } catch (RuntimeException e) {      // a share with extras that do not unpack
            Log.w(TAG, "could not read what was sent", e);
            return "";
        }
        if (uri == null) {
            return "";
        }
        ContentResolver resolver = activity.getContentResolver();
        File copy = new File(folder, nameOf(resolver, uri));
        try (InputStream in = resolver.openInputStream(uri);
             OutputStream out = new FileOutputStream(copy)) {
            if (in == null) {
                throw new IOException("nothing to read");
            }
            byte[] buffer = new byte[64 * 1024];
            long total = 0;
            for (int count; (count = in.read(buffer)) != -1; ) {
                total += count;
                if (total > MOST) {
                    throw new IOException("larger than any torrent");
                }
                out.write(buffer, 0, count);
            }
            return copy.getAbsolutePath();
        } catch (IOException | RuntimeException e) {     // SecurityException among them
            Log.w(TAG, "could not copy " + uri, e);
            copy.delete();
            return FAILED;
        }
    }

    /** The file an intent is about: the one it views, or the one it sends.
     * Only content addresses - a file: one could name Grabbit's own private
     * files, which this would then read on another app's behalf. */
    private static Uri address(Intent intent) {
        Uri uri = null;
        if (Intent.ACTION_VIEW.equals(intent.getAction())) {
            uri = intent.getData();
        } else if (Intent.ACTION_SEND.equals(intent.getAction())) {
            uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
        }
        return uri != null && ContentResolver.SCHEME_CONTENT.equals(uri.getScheme()) ? uri : null;
    }

    /** The name the other app gives the file, made safe to write here and
     * ending in .torrent, as analyze.py looks for. */
    private static String nameOf(ContentResolver resolver, Uri uri) {
        String name = null;
        try (Cursor cursor = resolver.query(uri, new String[] {OpenableColumns.DISPLAY_NAME},
                                            null, null, null)) {
            if (cursor != null && cursor.moveToFirst() && !cursor.isNull(0)) {
                name = cursor.getString(0);
            }
        } catch (RuntimeException e) {
            Log.w(TAG, "no name for " + uri, e);
        }
        if (name == null || name.isEmpty()) {
            name = uri.getLastPathSegment();
        }
        name = name == null ? "" : new File(name.replace('\\', '/')).getName();
        if (name.startsWith(".")) {
            name = name.replaceFirst("^\\.+", "");
        }
        if (!name.toLowerCase(Locale.ROOT).endsWith(".torrent")) {
            name = (name.isEmpty() ? "opened" : name) + ".torrent";
        }
        // Inside the 255 bytes a file name may have, at four bytes a character.
        if (name.codePointCount(0, name.length()) > 60) {
            name = name.substring(0, name.offsetByCodePoints(0, 52)) + ".torrent";
        }
        return name;
    }
}
