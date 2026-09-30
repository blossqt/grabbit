package com.grabbit.downloader;

import android.app.Activity;
import android.content.res.Configuration;
import android.graphics.drawable.ColorDrawable;
import android.os.Build;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.view.WindowInsetsController;

/**
 * The phone's look, for the app to be drawn in: the colours Android makes from
 * the wallpaper, whether the phone is set to dark, and the system bars
 * coloured to match the app. Here rather than in Python because reading
 * Android's resource classes or a View through pyjnius reads the whole class
 * first - a noticeable pause as the app opens.
 */
public class Look {
    private static final String TAG = "GrabbitLook";

    /**
     * Android's five tonal palettes made from the wallpaper - accent 1, 2
     * and 3, neutral 1 and 2 - each at its thirteen steps from white to black
     * (0, 10, 50, 100 ... 900, 1000), as ARGB. Empty before Android 12,
     * which makes none.
     */
    public static int[] palettes(Activity activity) {
        if (Build.VERSION.SDK_INT < 31) {
            return new int[0];
        }
        int[] ids = {
            android.R.color.system_accent1_0, android.R.color.system_accent1_10, android.R.color.system_accent1_50, android.R.color.system_accent1_100,
            android.R.color.system_accent1_200, android.R.color.system_accent1_300, android.R.color.system_accent1_400, android.R.color.system_accent1_500,
            android.R.color.system_accent1_600, android.R.color.system_accent1_700, android.R.color.system_accent1_800, android.R.color.system_accent1_900, android.R.color.system_accent1_1000,
            android.R.color.system_accent2_0, android.R.color.system_accent2_10, android.R.color.system_accent2_50, android.R.color.system_accent2_100,
            android.R.color.system_accent2_200, android.R.color.system_accent2_300, android.R.color.system_accent2_400, android.R.color.system_accent2_500,
            android.R.color.system_accent2_600, android.R.color.system_accent2_700, android.R.color.system_accent2_800, android.R.color.system_accent2_900, android.R.color.system_accent2_1000,
            android.R.color.system_accent3_0, android.R.color.system_accent3_10, android.R.color.system_accent3_50, android.R.color.system_accent3_100,
            android.R.color.system_accent3_200, android.R.color.system_accent3_300, android.R.color.system_accent3_400, android.R.color.system_accent3_500,
            android.R.color.system_accent3_600, android.R.color.system_accent3_700, android.R.color.system_accent3_800, android.R.color.system_accent3_900, android.R.color.system_accent3_1000,
            android.R.color.system_neutral1_0, android.R.color.system_neutral1_10, android.R.color.system_neutral1_50, android.R.color.system_neutral1_100,
            android.R.color.system_neutral1_200, android.R.color.system_neutral1_300, android.R.color.system_neutral1_400, android.R.color.system_neutral1_500,
            android.R.color.system_neutral1_600, android.R.color.system_neutral1_700, android.R.color.system_neutral1_800, android.R.color.system_neutral1_900, android.R.color.system_neutral1_1000,
            android.R.color.system_neutral2_0, android.R.color.system_neutral2_10, android.R.color.system_neutral2_50, android.R.color.system_neutral2_100,
            android.R.color.system_neutral2_200, android.R.color.system_neutral2_300, android.R.color.system_neutral2_400, android.R.color.system_neutral2_500,
            android.R.color.system_neutral2_600, android.R.color.system_neutral2_700, android.R.color.system_neutral2_800, android.R.color.system_neutral2_900, android.R.color.system_neutral2_1000
        };
        int[] colors = new int[ids.length];
        try {
            for (int i = 0; i < ids.length; i++) {
                colors[i] = activity.getColor(ids[i]);
            }
            return colors;
        } catch (RuntimeException e) {
            Log.w(TAG, "could not read the wallpaper colours", e);
            return new int[0];
        }
    }

    /** Whether the phone is set to its dark theme. */
    public static boolean night(Activity activity) {
        int mode = activity.getResources().getConfiguration().uiMode
                & Configuration.UI_MODE_NIGHT_MASK;
        return mode == Configuration.UI_MODE_NIGHT_YES;
    }

    /**
     * The window behind the app, and the status and navigation bars, in the
     * app's own background colour - with dark icons on them when that colour
     * is light. Whatever part of the window the app does not draw, behind a
     * keyboard as it slides in, shows this rather than black.
     */
    public static void paint(final Activity activity, final int color, final boolean light) {
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    Window window = activity.getWindow();
                    window.setBackgroundDrawable(new ColorDrawable(color));
                    window.setStatusBarColor(color);
                    window.setNavigationBarColor(color);
                    if (Build.VERSION.SDK_INT >= 30) {
                        WindowInsetsController bars = window.getInsetsController();
                        if (bars != null) {
                            int both = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
                            bars.setSystemBarsAppearance(light ? both : 0, both);
                        }
                    } else {
                        View decor = window.getDecorView();
                        int both = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
                                | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
                        int flags = decor.getSystemUiVisibility();
                        decor.setSystemUiVisibility(light ? flags | both : flags & ~both);
                    }
                } catch (RuntimeException e) {
                    Log.w(TAG, "could not colour the window", e);
                }
            }
        });
    }
}
