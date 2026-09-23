package com.grabbit.downloader;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.util.Log;
import android.webkit.MimeTypeMap;

import java.io.File;
import java.io.FileNotFoundException;
import java.util.ArrayList;
import java.util.Locale;

/**
 * Hands finished downloads to other apps: a player to open one, the share
 * sheet to send them on.
 *
 * Android stopped letting one app give another a path to a file long ago.
 * What it gives instead is an address this provider answers for, with leave
 * to read that one file and nothing else. The provider is not exported, so no
 * app can ask it for an address it was not given; and the addresses only ever
 * reach shared storage, where the downloads are - never the app's own private
 * files, where the download list and the downloader's secret live.
 *
 * open and share are what the window calls (grabbit_mobile/bootstrap.py);
 * build_apk.sh declares the provider in the manifest.
 */
public class FileShare extends ContentProvider {
    private static final String TAG = "GrabbitFiles";
    private static final String[] COLUMNS = {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};

    // What open and share answer, besides "" for done.
    public static final String GONE = "gone";           // the file is not there
    public static final String NO_APP = "no-app";       // nothing installed takes it
    public static final String FAILED = "failed";

    // ---------------------------------------------------------- the window's

    /** Open a file in whatever the phone opens its kind with - Android asks
     * which, if more than one app can and none is the default. */
    public static String open(Activity activity, String path) {
        File file = new File(path);
        if (!file.isFile()) {
            return GONE;
        }
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(address(activity, file), typeOf(file.getName(), "*/*"));
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return start(activity, intent);
    }

    /** Android's share sheet, for one file or several. */
    public static String share(Activity activity, String[] paths) {
        ArrayList<Uri> addresses = new ArrayList<>();
        String type = null;
        for (String path : paths) {
            File file = new File(path);
            if (file.isFile()) {
                addresses.add(address(activity, file));
                type = common(type, typeOf(file.getName(), "application/octet-stream"));
            }
        }
        if (addresses.isEmpty()) {
            return GONE;
        }
        Intent intent;
        if (addresses.size() == 1) {
            intent = new Intent(Intent.ACTION_SEND);
            intent.putExtra(Intent.EXTRA_STREAM, addresses.get(0));
        } else {
            intent = new Intent(Intent.ACTION_SEND_MULTIPLE);
            intent.putParcelableArrayListExtra(Intent.EXTRA_STREAM, addresses);
        }
        intent.setType(type);
        // The leave to read travels with the clip, and the share sheet passes
        // it on to whichever app is picked - and uses it to show a preview.
        ClipData clip = ClipData.newRawUri("", addresses.get(0));
        for (int i = 1; i < addresses.size(); i++) {
            clip.addItem(new ClipData.Item(addresses.get(i)));
        }
        intent.setClipData(clip);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return start(activity, Intent.createChooser(intent, null));
    }

    private static String start(Activity activity, Intent intent) {
        try {
            activity.startActivity(intent);
            return "";
        } catch (ActivityNotFoundException e) {
            return NO_APP;
        } catch (RuntimeException e) {
            Log.w(TAG, "could not hand a file over", e);
            return FAILED;
        }
    }

    static Uri address(Context context, File file) {
        return new Uri.Builder().scheme("content").authority(context.getPackageName() + ".files")
                .path(file.getAbsolutePath()).build();
    }

    static String typeOf(String name, String otherwise) {
        int dot = name == null ? -1 : name.lastIndexOf('.');
        if (dot >= 0) {
            String type = MimeTypeMap.getSingleton()
                    .getMimeTypeFromExtension(name.substring(dot + 1).toLowerCase(Locale.ROOT));
            if (type != null) {
                return type;
            }
        }
        return otherwise;
    }

    /** One type for several files: theirs if they share it, video/* for
     * videos of different kinds, and anything at all otherwise. */
    static String common(String kind, String other) {
        if (kind == null || kind.equals(other)) {
            return other;
        }
        int slash = kind.indexOf('/');
        if (slash > 0 && other.startsWith(kind.substring(0, slash + 1))) {
            return kind.substring(0, slash) + "/*";
        }
        return "*/*";
    }

    // --------------------------------------------------- the provider's own

    /** The file an address stands for - if it is one this provider gives out. */
    private static File file(Uri uri) throws FileNotFoundException {
        String path = uri.getPath();
        if (path == null || !path.startsWith("/storage/") || path.contains("/../")
                || path.endsWith("/..")) {
            throw new FileNotFoundException(String.valueOf(path));
        }
        File file = new File(path);
        if (!file.isFile()) {
            throw new FileNotFoundException(path);
        }
        return file;
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        return typeOf(uri.getLastPathSegment(), "application/octet-stream");
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs,
                        String sortOrder) {
        File file;
        try {
            file = file(uri);
        } catch (FileNotFoundException e) {
            return null;
        }
        // Only the two columns every app may ask for: the name and the size.
        ArrayList<String> names = new ArrayList<>();
        ArrayList<Object> values = new ArrayList<>();
        for (String column : projection == null ? COLUMNS : projection) {
            if (OpenableColumns.DISPLAY_NAME.equals(column)) {
                names.add(column);
                values.add(file.getName());
            } else if (OpenableColumns.SIZE.equals(column)) {
                names.add(column);
                values.add(file.length());
            }
        }
        MatrixCursor cursor = new MatrixCursor(names.toArray(new String[0]), 1);
        cursor.addRow(values.toArray());
        return cursor;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (mode != null && !mode.equals("r")) {
            throw new FileNotFoundException("downloads are handed over to read, not to change");
        }
        return ParcelFileDescriptor.open(file(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        return null;
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        return 0;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        return 0;
    }
}
