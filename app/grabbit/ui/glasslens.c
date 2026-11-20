/* The suite's liquid glass, pixel by pixel (suitetheme._lens loads this as glasslens.dll).

   Each pixel of the glass shows what lies behind a little way off from it, as light bending
   through a pane whose face is gently domed and whose edge is a rounded bevel:

   - the dome (dome): across the whole pane, what is behind is drawn in towards the middle, most at
     the middle and not at all at the edge, so the middle magnifies a little and straight lines
     bow, as through a lens;
   - the bevel (band, reach): within band of the edge, what lies beyond the edge is bent in, by
     reach at the very rim and less further in - steep at the rim and flat where it meets the
     face, as through a rounded edge;
   - colour (spread): red is bent that much further and blue that much less, so colours part
     where the bending is strong (where they would part by less than a third of a pixel, all
     three are read from one place).

   Shots are 32-bit Qt images (Format_RGB32 or ARGB32_Premultiplied: B, G, R, A in memory); the
   glass is written as ARGB32_Premultiplied, opaque inside the pane's rounded shape and clear
   outside it. With rim_only, only the bevel is written (a framed window's rim), the rest clear.
   The rows are shared among a few threads.

   Build: Hanami's build\build_glasslens.ps1 (MSYS2's GCC):
     gcc -O3 -march=x86-64-v2 -ffast-math -shared -static-libgcc -s -o glasslens.dll glasslens.c
*/
#include <math.h>
#include <stdint.h>
#include <string.h>
#include <windows.h>

#define EXPORT __declspec(dllexport)
#define MOST_THREADS 8

static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

typedef struct {
    const uint8_t *src; int sw, sh, sstride;
    uint8_t *dst; int width, height, dstride;
    float ox, oy, radius, band, reach, dome, spread;
    int rim_only;
    int first, last;                                 /* the rows this thread draws */
} Job;

/* Where (x, y) falls among the shot's pixels: the four around it and how far between them. */
typedef struct { const uint8_t *a, *b, *c, *d; float fx, fy; } Spot;

static inline Spot spot(const Job *j, float x, float y)
{
    x = clampf(x - 0.5f, 0.0f, (float)(j->sw - 1));
    y = clampf(y - 0.5f, 0.0f, (float)(j->sh - 1));
    int x0 = (int)x, y0 = (int)y;
    int x1 = x0 + 1 < j->sw ? x0 + 1 : x0, y1 = y0 + 1 < j->sh ? y0 + 1 : y0;
    const uint8_t *r0 = j->src + (size_t)y0 * j->sstride, *r1 = j->src + (size_t)y1 * j->sstride;
    Spot s = { r0 + x0 * 4, r0 + x1 * 4, r1 + x0 * 4, r1 + x1 * 4, x - (float)x0, y - (float)y0 };
    return s;
}

static inline float channel(Spot s, int c)          /* 0 blue, 1 green, 2 red */
{
    float top = s.a[c] + (s.b[c] - s.a[c]) * s.fx;
    float bottom = s.c[c] + (s.d[c] - s.c[c]) * s.fx;
    return top + (bottom - top) * s.fy;
}

static DWORD WINAPI draw_rows(LPVOID arg)
{
    const Job *j = (const Job *)arg;
    const float cx = j->width * 0.5f, cy = j->height * 0.5f;
    const float inner_x = cx - j->radius, inner_y = cy - j->radius;
    const int solid = (int)ceilf(j->band + j->radius) + 1;
    const float band = j->band, reach = j->reach, dome = j->dome, spread = j->spread;

    for (int py = j->first; py < j->last; ++py) {
        uint32_t *out = (uint32_t *)(j->dst + (size_t)py * j->dstride);
        const float y = py + 0.5f;
        const int middle_row = py >= solid && py < j->height - solid;
        for (int px = 0; px < j->width; ++px) {
            if (j->rim_only && middle_row && px == solid && j->width - solid > solid) {
                memset(out + px, 0, (size_t)(j->width - 2 * solid) * 4);   /* the window shows there */
                px = j->width - solid - 1;
                continue;
            }
            const float x = px + 0.5f;
            /* signed distance to the rounded edge (negative inside), and which way is out */
            float qx = fabsf(x - cx) - inner_x, qy = fabsf(y - cy) - inner_y;
            float mx = fmaxf(qx, 0.0f), my = fmaxf(qy, 0.0f);
            float outside = sqrtf(mx * mx + my * my) + fminf(fmaxf(qx, qy), 0.0f) - j->radius;
            if (outside > 0.0f) {                    /* beyond the rounded corner: clear */
                out[px] = 0;
                continue;
            }
            float in = -outside;                     /* how far in from the edge */
            float dx = 0.0f, dy = 0.0f;              /* how far off what this pixel shows lies */
            if (in < band) {
                float nx, ny;                        /* the outward direction */
                if (qx > 0.0f && qy > 0.0f) {
                    float l = sqrtf(qx * qx + qy * qy);
                    nx = qx / l; ny = qy / l;
                } else if (qx > qy) {
                    nx = 1.0f; ny = 0.0f;
                } else {
                    nx = 0.0f; ny = 1.0f;
                }
                if (x < cx) nx = -nx;
                if (y < cy) ny = -ny;
                float u = 1.0f - in / band;          /* 1 at the rim, 0 where the bevel meets the face */
                float s = reach * (1.0f - sqrtf(fmaxf(0.0f, 1.0f - u * u)));
                dx = nx * s; dy = ny * s;
            } else if (j->rim_only) {
                out[px] = 0;
                continue;
            }
            if (dome > 0.0f) {
                /* the face: towards the middle, by dome at the middle and nothing at the edge */
                float ex = (x - cx) / cx, ey = (y - cy) / cy;
                float pull = dome * (1.0f - fminf(1.0f, ex * ex + ey * ey));
                dx -= (x - cx) * pull; dy -= (y - cy) * pull;
            }
            float sx = j->ox + x, sy = j->oy + y;
            Spot g = spot(j, sx + dx, sy + dy);
            float r, gg = channel(g, 1), b;
            if (spread != 0.0f && (fabsf(dx) + fabsf(dy)) * spread > 0.33f) {
                r = channel(spot(j, sx + dx * (1.0f + spread), sy + dy * (1.0f + spread)), 2);
                b = channel(spot(j, sx + dx * (1.0f - spread), sy + dy * (1.0f - spread)), 0);
            } else {
                r = channel(g, 2);
                b = channel(g, 0);
            }
            out[px] = 0xFF000000u | ((uint32_t)(r + 0.5f) << 16) | ((uint32_t)(gg + 0.5f) << 8) | (uint32_t)(b + 0.5f);
        }
    }
    return 0;
}

/* The glass for a pane width x height (in the glass's own pixels) whose top left lies at (ox, oy)
   in the shot. radius: its corners; band, reach: its bevel; dome: how much its face draws what is
   behind in towards the middle (0.15: the middle shows things about 18% larger); spread: how much
   further red bends than green, and blue less. Returns 0, or -1 if the sizes make no sense. */
EXPORT int glass_lens(const uint8_t *src, int sw, int sh, int sstride,
                      uint8_t *dst, int width, int height, int dstride,
                      float ox, float oy, float radius, float band, float reach,
                      float dome, float spread, int rim_only)
{
    if (!src || !dst || sw < 1 || sh < 1 || width < 1 || height < 1)
        return -1;
    float half = fminf(width, height) * 0.5f;
    Job base = { src, sw, sh, sstride, dst, width, height, dstride, ox, oy,
                 clampf(radius, 0.0f, half), clampf(band, 0.0f, half), reach, dome, spread, rim_only, 0, height };

    /* about one thread for every 60,000 pixels drawn, up to the processors there are */
    SYSTEM_INFO info;
    GetSystemInfo(&info);
    long drawn = rim_only ? (long)(width + height) * 2 * (long)(band + radius + 2) : (long)width * height;
    int threads = (int)(drawn / 60000) + 1;
    if (threads > (int)info.dwNumberOfProcessors) threads = (int)info.dwNumberOfProcessors;
    if (threads > MOST_THREADS) threads = MOST_THREADS;
    if (threads < 2) {
        draw_rows(&base);
        return 0;
    }
    Job jobs[MOST_THREADS];
    HANDLE handles[MOST_THREADS];
    int made = 0;
    for (int t = 0; t < threads; ++t) {
        jobs[t] = base;
        jobs[t].first = height * t / threads;
        jobs[t].last = height * (t + 1) / threads;
        if (t == threads - 1)
            break;                                   /* the last share is this thread's own */
        handles[made] = CreateThread(NULL, 0, draw_rows, &jobs[t], 0, NULL);
        if (!handles[made])
            draw_rows(&jobs[t]);                     /* no thread to be had: drawn here instead */
        else
            ++made;
    }
    draw_rows(&jobs[threads - 1]);
    if (made) {
        WaitForMultipleObjects((DWORD)made, handles, TRUE, INFINITE);
        for (int t = 0; t < made; ++t)
            CloseHandle(handles[t]);
    }
    return 0;
}
