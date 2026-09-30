package com.grabbit.downloader;

import android.app.Activity;
import android.util.Log;
import android.view.Display;
import android.view.Window;
import android.view.WindowManager;

/**
 * Asks for the screen's fastest refresh rate. A phone that can show 90 or
 * 120 frames a second leaves an app that does not ask at 60, and the app's
 * window is a surface Android cannot tell is moving. The fastest mode at the
 * screen's present resolution is asked for, so nothing is resized; Android
 * still has the last word - battery saver, or a phone set to 60, keep it lower.
 */
public class Screen {
    private static final String TAG = "GrabbitScreen";

    /** The refresh rate asked for, in frames a second, or 0 if none could be. */
    public static float fastest(final Activity activity) {
        try {
            Display display = activity.getWindowManager().getDefaultDisplay();
            Display.Mode current = display.getMode();
            Display.Mode best = current;
            for (Display.Mode mode : display.getSupportedModes()) {
                if (mode.getPhysicalWidth() == current.getPhysicalWidth()
                        && mode.getPhysicalHeight() == current.getPhysicalHeight()
                        && mode.getRefreshRate() > best.getRefreshRate()) {
                    best = mode;
                }
            }
            final int id = best.getModeId();
            activity.runOnUiThread(new Runnable() {
                @Override
                public void run() {
                    try {
                        Window window = activity.getWindow();
                        WindowManager.LayoutParams attributes = window.getAttributes();
                        attributes.preferredDisplayModeId = id;
                        window.setAttributes(attributes);
                    } catch (RuntimeException e) {
                        Log.w(TAG, "could not ask for the faster refresh rate", e);
                    }
                }
            });
            return best.getRefreshRate();
        } catch (RuntimeException e) {
            Log.w(TAG, "could not read the screen's refresh rates", e);
            return 0f;
        }
    }
}
