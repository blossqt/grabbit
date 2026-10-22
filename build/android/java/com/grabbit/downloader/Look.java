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
 * The phone's look, for the app to be drawn in: whether the phone is set to
 * dark, and the system bars coloured to match the app. The colours themselves
 * are the suite's pink, never the wallpaper's (ui/theme.py). Here rather than
 * in Python because reading Android's resource classes or a View through
 * pyjnius reads the whole class first - a noticeable pause as the app opens.
 */
public class Look {
    private static final String TAG = "GrabbitLook";

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
