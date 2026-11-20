/* Live glass for a framed window: whatever is behind it, bent live (built into glasslens.dll
   with glasslens.c; suitetheme's _LiveGlass drives it).

   A framed window's glass is see-through, drawn by Windows itself: no program can read what lies
   behind its own window as it changes (only by leaving the window out of screen capture, which
   stops NVIDIA's desktop recording - the suite never does). So a helper window, the same size, is
   kept just behind the window, and into the helper go DWM thumbnails - the live pictures Alt+Tab
   and the taskbar show - of the windows behind, the desktop's included. DWM draws them, live,
   video and all, each scaled from the part of the screen the lens says:

   - the face: one thumbnail a window, what is behind a little larger (face_zoom), as through a
     slightly domed pane;
   - the bevel: thin lines along each edge, each from a little further out than where it lies -
     steep at the rim, none where it meets the face - as through a rounded edge.

   A thumbnail is an axis-aligned rectangle scaled into another, so the face cannot bow lines as
   glass_lens does for a pop-up, and colours cannot part. Registering thumbnails is slow (about a
   third of a millisecond each), so every window that shows anywhere on the window's screen is
   registered once, bottom first (a window above is registered later and drawn over those below);
   only a change in which windows show, or their order, registers them again. Moving updates the
   rectangles, which is quick.

   glass_live_open(helper, window) -> state; glass_live_update(state, ...) as often as wanted (it
   changes nothing that has not changed); glass_live_close(state). Sizes in physical pixels. */
#include <dwmapi.h>
#include <math.h>
#include <string.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)
#define LIVE_MOST_LINES 24           /* bevel lines along each edge */
#define LIVE_MOST_TILES (1 + 4 * LIVE_MOST_LINES)
#define LIVE_MOST_WINDOWS 48
#define LIVE_LINE 4                  /* the bevel's lines are this many pixels across, or more */

typedef struct { float x0, y0, x1, y1; float sx0, sy0, sx1, sy1; } LiveTile;   /* in the window's pixels */
typedef struct { HTHUMBNAIL thumb; RECT dest, from; BOOL shown; } LiveThumb;

typedef struct {
    HWND helper, window;
    int w, h;
    float band, reach, zoom;
    LiveTile tiles[LIVE_MOST_TILES];
    int ntiles;
    HWND stack[LIVE_MOST_WINDOWS];                       /* bottom first, as registered */
    LiveThumb thumbs[LIVE_MOST_WINDOWS][LIVE_MOST_TILES];
    int nstack;
} Live;

/* How far out from the edge a point d in from it takes its light: steep at the rim, flat where
   the bevel meets the face. */
static float bevel_shift(float d, float band, float reach)
{
    if (band <= 0.0f || d >= band) return 0.0f;
    float u = 1.0f - d / band;
    return reach * (1.0f - sqrtf(fmaxf(0.0f, 1.0f - u * u)));
}

/* The face, and lines of the bevel round it: the top and bottom lines run the whole width (their
   ends are the corners), the side lines between them. */
static void make_tiles(Live *g)
{
    float w = (float)g->w, h = (float)g->h, cx = w * 0.5f, cy = h * 0.5f, k = 1.0f / g->zoom;
    float band = fminf(g->band, fminf(w, h) * 0.3f);
    int lines = (int)ceilf(band / LIVE_LINE);
    if (lines > LIVE_MOST_LINES) lines = LIVE_MOST_LINES;
    float step = lines ? band / lines : 0.0f;
    g->ntiles = 0;
    /* the face: what is behind it, a little larger */
    LiveTile *f = &g->tiles[g->ntiles++];
    f->x0 = band; f->y0 = band; f->x1 = w - band; f->y1 = h - band;
    f->sx0 = cx + (f->x0 - cx) * k; f->sx1 = cx + (f->x1 - cx) * k;
    f->sy0 = cy + (f->y0 - cy) * k; f->sy1 = cy + (f->y1 - cy) * k;
    for (int i = 0; i < lines; ++i) {
        float d0 = i * step, d1 = (i + 1) * step;
        float out0 = bevel_shift(d0, band, g->reach), out1 = bevel_shift(d1, band, g->reach);
        float along = (out0 + out1) * 0.5f;              /* squeezed in a little along the edge too */
        float wide = (1.0f + 2.0f * along / w) * k, tall = (1.0f + 2.0f * along / h) * k;
        LiveTile *t;
        /* top and bottom: the whole width */
        t = &g->tiles[g->ntiles++];
        t->x0 = 0; t->x1 = w; t->y0 = d0; t->y1 = d1;
        t->sx0 = cx - cx * wide; t->sx1 = cx + cx * wide;
        t->sy0 = cy + (d0 - cy) * k - out0; t->sy1 = cy + (d1 - cy) * k - out1;
        t = &g->tiles[g->ntiles++];
        t->x0 = 0; t->x1 = w; t->y0 = h - d1; t->y1 = h - d0;
        t->sx0 = cx - cx * wide; t->sx1 = cx + cx * wide;
        t->sy0 = cy + (h - d1 - cy) * k + out1; t->sy1 = cy + (h - d0 - cy) * k + out0;
        /* the sides, between the top and bottom bevels */
        t = &g->tiles[g->ntiles++];
        t->x0 = d0; t->x1 = d1; t->y0 = band; t->y1 = h - band;
        t->sx0 = cx + (d0 - cx) * k - out0; t->sx1 = cx + (d1 - cx) * k - out1;
        t->sy0 = cy + (band - cy) * tall; t->sy1 = cy + (h - band - cy) * tall;
        t = &g->tiles[g->ntiles++];
        t->x0 = w - d1; t->x1 = w - d0; t->y0 = band; t->y1 = h - band;
        t->sx0 = cx + (w - d1 - cx) * k + out1; t->sx1 = cx + (w - d0 - cx) * k + out0;
        t->sy0 = cy + (band - cy) * tall; t->sy1 = cy + (h - band - cy) * tall;
    }
}

static BOOL cloaked(HWND hwnd)
{
    int value = 0;
    return SUCCEEDED(DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, &value, sizeof value)) && value;
}

static void forget(Live *g)
{
    for (int s = 0; s < g->nstack; ++s)
        for (int t = 0; t < LIVE_MOST_TILES; ++t)
            if (g->thumbs[s][t].thumb) DwmUnregisterThumbnail(g->thumbs[s][t].thumb);
    memset(g->thumbs, 0, sizeof g->thumbs);
    g->nstack = 0;
}

EXPORT void *glass_live_open(HWND helper, HWND window)
{
    Live *g = (Live *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(Live));
    if (g) { g->helper = helper; g->window = window; }
    return g;
}

EXPORT void glass_live_close(void *state)
{
    Live *g = (Live *)state;
    if (!g) return;
    forget(g);
    HeapFree(GetProcessHeap(), 0, g);
}

/* Keep the helper just behind the window and its thumbnails showing what the glass bends in.
   band, reach: the bevel; zoom: how much larger the face shows what is behind (1.05: 5%).
   Returns how many thumbnails are shown, or -1. */
EXPORT int glass_live_update(void *state, float band, float reach, float zoom)
{
    Live *g = (Live *)state;
    if (!g || !IsWindow(g->window) || !IsWindow(g->helper)) return -1;
    /* minimised, hidden, or on another desktop: so is the helper */
    if (!IsWindowVisible(g->window) || IsIconic(g->window) || cloaked(g->window)) {
        if (IsWindowVisible(g->helper)) ShowWindowAsync(g->helper, SW_HIDE);
        return 0;
    }
    RECT at;
    GetWindowRect(g->window, &at);
    int w = at.right - at.left, h = at.bottom - at.top;
    if (w < 1 || h < 1) return -1;
    if (zoom < 1.0f) zoom = 1.0f;

    RECT hat;
    GetWindowRect(g->helper, &hat);
    if (!IsWindowVisible(g->helper) || !EqualRect(&hat, &at) || GetWindow(g->window, GW_HWNDNEXT) != g->helper)
        SetWindowPos(g->helper, g->window, at.left, at.top, w, h,
                     SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_ASYNCWINDOWPOS | SWP_SHOWWINDOW);

    if (w != g->w || h != g->h || band != g->band || reach != g->reach || zoom != g->zoom) {
        g->w = w; g->h = h; g->band = band; g->reach = reach; g->zoom = zoom;
        make_tiles(g);
        for (int s = 0; s < g->nstack; ++s)
            for (int t = 0; t < LIVE_MOST_TILES; ++t) g->thumbs[s][t].shown = g->thumbs[s][t].shown && t < g->ntiles;
    }

    /* the windows that show anywhere on this screen, below the window, top first */
    HMONITOR monitor = MonitorFromWindow(g->window, MONITOR_DEFAULTTONEAREST);
    MONITORINFO mi = { sizeof mi };
    GetMonitorInfoW(monitor, &mi);
    HWND found[LIVE_MOST_WINDOWS];
    int nfound = 0;
    HRGN left = CreateRectRgnIndirect(&mi.rcMonitor);
    {   /* and whatever of the window, and what its bevel reaches, lies on another screen */
        LONG out = (LONG)ceilf(reach) + 2;
        RECT need = { at.left - out, at.top - out, at.right + out, at.bottom + out };
        HRGN more = CreateRectRgnIndirect(&need);
        CombineRgn(left, left, more, RGN_OR);
        DeleteObject(more);
    }
    for (HWND w2 = GetWindow(g->window, GW_HWNDNEXT); w2 && nfound < LIVE_MOST_WINDOWS; w2 = GetWindow(w2, GW_HWNDNEXT)) {
        if (w2 == g->helper || !IsWindowVisible(w2) || IsIconic(w2) || cloaked(w2)) continue;
        RECT r;
        GetWindowRect(w2, &r);
        if (r.right - r.left < 1 || r.bottom - r.top < 1 || !RectInRegion(left, &r)) continue;
        found[nfound++] = w2;
        if (!(GetWindowLongW(w2, GWL_EXSTYLE) & WS_EX_LAYERED)) {       /* a layered one may be see-through */
            HRGN its = CreateRectRgnIndirect(&r);
            int kind = CombineRgn(left, left, its, RGN_DIFF);
            DeleteObject(its);
            if (kind == NULLREGION) break;
        }
    }
    DeleteObject(left);

    BOOL same = nfound == g->nstack;
    for (int k = 0; same && k < nfound; ++k)
        same = found[nfound - 1 - k] == g->stack[k];
    if (!same) {
        forget(g);
        for (int k = nfound - 1; k >= 0; --k) {
            int s = g->nstack++;
            g->stack[s] = found[k];
            for (int t = 0; t < LIVE_MOST_TILES; ++t)
                if (FAILED(DwmRegisterThumbnail(g->helper, found[k], &g->thumbs[s][t].thumb)))
                    g->thumbs[s][t].thumb = NULL;
        }
    }

    int shown = 0;
    for (int s = 0; s < g->nstack; ++s) {
        RECT sr;
        GetWindowRect(g->stack[s], &sr);
        for (int ti = 0; ti < LIVE_MOST_TILES; ++ti) {
            LiveThumb *lt = &g->thumbs[s][ti];
            if (!lt->thumb) continue;
            DWM_THUMBNAIL_PROPERTIES p;
            memset(&p, 0, sizeof p);
            BOOL show = FALSE;
            RECT dest = { 0 }, from = { 0 };
            if (ti < g->ntiles) {
                const LiveTile *t = &g->tiles[ti];
                float ax0 = at.left + t->sx0, ay0 = at.top + t->sy0, ax1 = at.left + t->sx1, ay1 = at.top + t->sy1;
                float ix0 = fmaxf(ax0, (float)sr.left), iy0 = fmaxf(ay0, (float)sr.top);
                float ix1 = fminf(ax1, (float)sr.right), iy1 = fminf(ay1, (float)sr.bottom);
                if (ix1 - ix0 >= 0.5f && iy1 - iy0 >= 0.5f && ax1 - ax0 > 0.01f && ay1 - ay0 > 0.01f) {
                    float fx = (t->x1 - t->x0) / (ax1 - ax0), fy = (t->y1 - t->y0) / (ay1 - ay0);
                    dest.left = lroundf(t->x0 + (ix0 - ax0) * fx); dest.top = lroundf(t->y0 + (iy0 - ay0) * fy);
                    dest.right = lroundf(t->x0 + (ix1 - ax0) * fx); dest.bottom = lroundf(t->y0 + (iy1 - ay0) * fy);
                    from.left = lroundf(ix0 - sr.left); from.top = lroundf(iy0 - sr.top);
                    from.right = lroundf(ix1 - sr.left); from.bottom = lroundf(iy1 - sr.top);
                    show = dest.right > dest.left && dest.bottom > dest.top;
                }
            }
            if (!show) {
                if (lt->shown) {
                    p.dwFlags = DWM_TNP_VISIBLE; p.fVisible = FALSE;
                    DwmUpdateThumbnailProperties(lt->thumb, &p);
                    lt->shown = FALSE;
                }
                continue;
            }
            ++shown;
            if (lt->shown && EqualRect(&dest, &lt->dest) && EqualRect(&from, &lt->from)) continue;
            p.dwFlags = DWM_TNP_RECTDESTINATION | DWM_TNP_RECTSOURCE | DWM_TNP_VISIBLE | DWM_TNP_OPACITY;
            p.rcDestination = dest; p.rcSource = from; p.fVisible = TRUE; p.opacity = 255;
            DwmUpdateThumbnailProperties(lt->thumb, &p);
            lt->dest = dest; lt->from = from; lt->shown = TRUE;
        }
    }
    return shown;
}
