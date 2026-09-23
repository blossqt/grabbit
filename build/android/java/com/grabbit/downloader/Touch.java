package com.grabbit.downloader;

import android.app.Activity;
import android.util.Log;
import android.view.HapticFeedbackConstants;

/**
 * The buzz of a long press, as Android's own lists give one when holding an
 * item picks it out. It follows the phone's touch-feedback setting, as theirs
 * does. Here rather than in Python because asking for a View through pyjnius
 * reads the whole class first, which is a noticeable pause on a phone.
 */
public class Touch {
    private static final String TAG = "GrabbitTouch";

    public static void held(final Activity activity) {
        activity.runOnUiThread(new Runnable() {
            @Override
            public void run() {
                try {
                    activity.getWindow().getDecorView()
                            .performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);
                } catch (RuntimeException e) {
                    Log.w(TAG, "no buzz for a long press", e);
                }
            }
        });
    }
}
