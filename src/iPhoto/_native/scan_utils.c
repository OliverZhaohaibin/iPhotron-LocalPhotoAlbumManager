#include <ctype.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <sys/stat.h>
#include <wchar.h>
#define EXPORT __declspec(dllexport)
#define strcasecmp _stricmp
#else
#include <dirent.h>
#include <errno.h>
#include <sys/stat.h>
#include <unistd.h>
#define EXPORT __attribute__((visibility("default")))
#endif

#define XXH_INLINE_ALL
#include "xxhash.h"

#define HASH_THRESHOLD (2 * 1024 * 1024ULL)
#define HASH_SAMPLE_SIZE (256 * 1024ULL)
#define HASH_STREAM_CHUNK (1024 * 1024ULL)

#define MEDIA_HINT_UNKNOWN (-1)
#define MEDIA_HINT_IMAGE 0
#define MEDIA_HINT_VIDEO 1

#define PAIR_TIME_DELTA_US 3000000LL
#define LIVE_DURATION_PREFERRED_MIN 1.0
#define LIVE_DURATION_PREFERRED_MAX 3.5

typedef int (*file_found_callback)(const char *path, const char *rel_path, void *userdata);

typedef struct DiscoveryConfig {
    const char **include_globs;
    size_t include_count;
    const char **exclude_globs;
    size_t exclude_count;
    const char **supported_exts;
    size_t supported_count;
    const char **skip_dir_names;
    size_t skip_dir_count;
    int skip_hidden_dirs;
    file_found_callback callback;
    void *userdata;
    int stopped;
} DiscoveryConfig;

typedef struct DiscoveryItem {
    char *abs_path;
    char *rel_path;
    int media_kind;
} DiscoveryItem;

typedef struct DiscoveryHandle {
    DiscoveryItem *items;
    size_t count;
    size_t capacity;
    size_t cursor;
    int failed;
} DiscoveryHandle;

typedef struct PrepareScanInput {
    const char *abs_path;
    const char *rel_path;
    int64_t size_bytes;
    int64_t mtime_us;
    const char *dt_value;
    int media_hint;
} PrepareScanInput;

typedef struct PrepareScanOutput {
    int ok;
    char file_id[33];
    int64_t ts;
    int year;
    int month;
    int media_type;
} PrepareScanOutput;

typedef struct PairRowInput {
    const char *rel;
    const char *mime;
    const char *dt;
    const char *content_id;
    double dur;
    double still_image_time;
    int has_dur;
    int has_still_image_time;
} PairRowInput;

typedef struct PairMatchOutput {
    uint32_t still_index;
    uint32_t motion_index;
    double confidence;
} PairMatchOutput;

typedef struct PairRowEntry {
    char *rel;
    char *mime;
    char *dt;
    char *content_id;
    char *normalised_content_id;
    char *stem;
    char *folder;
    double dur;
    double still_image_time;
    int has_dur;
    int has_still_image_time;
    int is_photo;
    int is_video;
    int has_dt;
    int64_t dt_us;
} PairRowEntry;

typedef struct PairContext {
    PairRowEntry *rows;
    size_t row_count;
    size_t row_capacity;
    PairMatchOutput *matches;
    size_t match_count;
    size_t match_capacity;
    size_t match_cursor;
    int finalised;
} PairContext;

static int parse_iso8601_core(const char *value, int64_t *unix_us, int *year_out, int *month_out);
static char *dup_string_len(const char *value, size_t length);
static char *dup_string(const char *value);
static int str_case_starts_with(const char *value, const char *prefix);
static int str_case_equals(const char *left, const char *right);
static char *copy_stem(const char *path);
static char *copy_folder(const char *path);
static int collect_discovery_callback(const char *path, const char *rel_path, void *userdata);
static int resolve_media_type(const char *rel_path, int media_hint);
static void free_pair_row_entry(PairRowEntry *row);
static int reserve_pair_rows(PairContext *ctx, size_t additional);
static int reserve_pair_matches(PairContext *ctx, size_t additional);
static int append_pair_match(PairContext *ctx, uint32_t still_index, uint32_t motion_index, double confidence);
static int pair_row_is_photo(const PairRowEntry *row);
static int pair_row_is_video(const PairRowEntry *row);
static double duration_score(double duration);
static int candidate_beats_best(const PairRowEntry *candidate, const PairRowEntry *best);
static int build_unique_indices(const PairContext *ctx, int want_photo, uint32_t **out_indices, size_t *out_count);
static uint32_t find_best_content_id_match(
    const PairContext *ctx,
    const PairRowEntry *photo,
    const uint32_t *video_indices,
    size_t video_count,
    const unsigned char *used_videos
);
static uint32_t match_by_time(
    const PairContext *ctx,
    const PairRowEntry *photo,
    const uint32_t *video_indices,
    size_t video_count,
    const unsigned char *used_videos,
    int match_mode
);
static int build_pair_matches(PairContext *ctx);
EXPORT int compute_file_id_c(const char *path, char *out_hex, size_t out_size);
EXPORT int normalise_content_id(const char *value, char *out, size_t out_size);
#ifdef _WIN32
static wchar_t *utf8_to_wide(const char *value);
static char *wide_to_utf8(const wchar_t *value);
static wchar_t *join_wide_path(const wchar_t *prefix, const wchar_t *name);
static void discover_windows_recursive(
    const wchar_t *abs_dir,
    const char *rel_dir,
    DiscoveryConfig *config,
    int *count
);
#else
static char *join_fs_path(const char *prefix, const char *name);
static void discover_posix_recursive(
    const char *abs_dir,
    const char *rel_dir,
    DiscoveryConfig *config,
    int *count
);
#endif

static void free_discovery_handle(DiscoveryHandle *handle) {
    size_t index;
    if (!handle) {
        return;
    }
    for (index = 0; index < handle->count; index++) {
        free(handle->items[index].abs_path);
        free(handle->items[index].rel_path);
    }
    free(handle->items);
    free(handle);
}

EXPORT int discover_files_c(
    const char *root_dir,
    const char **include_globs,
    size_t include_count,
    const char **exclude_globs,
    size_t exclude_count,
    const char **supported_exts,
    size_t supported_count,
    const char **skip_dir_names,
    size_t skip_dir_count,
    int skip_hidden_dirs,
    file_found_callback callback,
    void *userdata
) {
    DiscoveryConfig config;
    int count = 0;

    if (!root_dir || !callback) {
        return 0;
    }

    memset(&config, 0, sizeof(config));
    config.include_globs = include_globs;
    config.include_count = include_count;
    config.exclude_globs = exclude_globs;
    config.exclude_count = exclude_count;
    config.supported_exts = supported_exts;
    config.supported_count = supported_count;
    config.skip_dir_names = skip_dir_names;
    config.skip_dir_count = skip_dir_count;
    config.skip_hidden_dirs = skip_hidden_dirs;
    config.callback = callback;
    config.userdata = userdata;
    config.stopped = 0;

#ifdef _WIN32
    {
        wchar_t *root_wide = utf8_to_wide(root_dir);
        if (!root_wide) {
            return 0;
        }
        discover_windows_recursive(root_wide, "", &config, &count);
        free(root_wide);
    }
#else
    discover_posix_recursive(root_dir, "", &config, &count);
#endif

    return count;
}

EXPORT void *discovery_open_c(
    const char *root_dir,
    const char **include_globs,
    size_t include_count,
    const char **exclude_globs,
    size_t exclude_count,
    const char **supported_exts,
    size_t supported_count,
    const char **skip_dir_names,
    size_t skip_dir_count,
    int skip_hidden_dirs
) {
    DiscoveryHandle *handle;
    DiscoveryConfig config;
    int count = 0;

    if (!root_dir) {
        return NULL;
    }

    handle = (DiscoveryHandle *)calloc(1, sizeof(DiscoveryHandle));
    if (!handle) {
        return NULL;
    }

    memset(&config, 0, sizeof(config));
    config.include_globs = include_globs;
    config.include_count = include_count;
    config.exclude_globs = exclude_globs;
    config.exclude_count = exclude_count;
    config.supported_exts = supported_exts;
    config.supported_count = supported_count;
    config.skip_dir_names = skip_dir_names;
    config.skip_dir_count = skip_dir_count;
    config.skip_hidden_dirs = skip_hidden_dirs;
    config.callback = collect_discovery_callback;
    config.userdata = handle;
    config.stopped = 0;

#ifdef _WIN32
    {
        wchar_t *root_wide = utf8_to_wide(root_dir);
        if (!root_wide) {
            free_discovery_handle(handle);
            return NULL;
        }
        discover_windows_recursive(root_wide, "", &config, &count);
        free(root_wide);
    }
#else
    discover_posix_recursive(root_dir, "", &config, &count);
#endif

    if (handle->failed) {
        free_discovery_handle(handle);
        return NULL;
    }

    return handle;
}

EXPORT int discovery_next_chunk_c(
    void *handle_ptr,
    size_t max_items,
    size_t max_bytes,
    DiscoveryItem **out_items,
    size_t *out_count,
    int *out_done
) {
    DiscoveryHandle *handle = (DiscoveryHandle *)handle_ptr;
    size_t start;
    size_t end;
    size_t bytes = 0;

    if (!handle || !out_items || !out_count || !out_done) {
        return 0;
    }

    if (max_items == 0) {
        max_items = 1;
    }

    start = handle->cursor;
    if (start >= handle->count) {
        *out_items = NULL;
        *out_count = 0;
        *out_done = 1;
        return 1;
    }

    end = start;
    while (end < handle->count && (end - start) < max_items) {
        size_t item_bytes = strlen(handle->items[end].abs_path) + strlen(handle->items[end].rel_path);
        if (end > start && max_bytes > 0 && (bytes + item_bytes) > max_bytes) {
            break;
        }
        bytes += item_bytes;
        end++;
    }
    if (end == start) {
        end++;
    }

    *out_items = handle->items + start;
    *out_count = end - start;
    handle->cursor = end;
    *out_done = handle->cursor >= handle->count ? 1 : 0;
    return 1;
}

EXPORT void discovery_close_c(void *handle_ptr) {
    free_discovery_handle((DiscoveryHandle *)handle_ptr);
}

EXPORT int prepare_scan_chunk_c(
    const PrepareScanInput *inputs,
    size_t count,
    PrepareScanOutput *outputs
) {
    size_t index;
    if ((!inputs && count > 0) || !outputs) {
        return 0;
    }

    for (index = 0; index < count; index++) {
        int64_t ts = 0;
        int year = 0;
        int month = 0;
        char file_id[33];
        memset(&outputs[index], 0, sizeof(outputs[index]));
        outputs[index].media_type = MEDIA_HINT_UNKNOWN;

        if (!inputs[index].abs_path || !inputs[index].rel_path) {
            continue;
        }
        if (!compute_file_id_c(inputs[index].abs_path, file_id, sizeof(file_id))) {
            continue;
        }

        if (inputs[index].dt_value && parse_iso8601_core(inputs[index].dt_value, &ts, &year, &month)) {
            outputs[index].ts = ts;
            outputs[index].year = year;
            outputs[index].month = month;
        } else {
            outputs[index].ts = inputs[index].mtime_us;
            outputs[index].year = 0;
            outputs[index].month = 0;
        }

        memcpy(outputs[index].file_id, file_id, sizeof(file_id));
        outputs[index].media_type = resolve_media_type(inputs[index].rel_path, inputs[index].media_hint);
        outputs[index].ok = 1;
    }

    return 1;
}

static void free_pair_row_entry(PairRowEntry *row) {
    if (!row) {
        return;
    }
    free(row->rel);
    free(row->mime);
    free(row->dt);
    free(row->content_id);
    free(row->normalised_content_id);
    free(row->stem);
    free(row->folder);
    memset(row, 0, sizeof(*row));
}

static int reserve_pair_rows(PairContext *ctx, size_t additional) {
    PairRowEntry *rows;
    size_t new_capacity;
    if (!ctx) {
        return 0;
    }
    if (ctx->row_count + additional <= ctx->row_capacity) {
        return 1;
    }
    new_capacity = ctx->row_capacity == 0 ? 256 : ctx->row_capacity;
    while (new_capacity < ctx->row_count + additional) {
        new_capacity *= 2;
    }
    rows = (PairRowEntry *)realloc(ctx->rows, new_capacity * sizeof(PairRowEntry));
    if (!rows) {
        return 0;
    }
    ctx->rows = rows;
    ctx->row_capacity = new_capacity;
    return 1;
}

static int reserve_pair_matches(PairContext *ctx, size_t additional) {
    PairMatchOutput *matches;
    size_t new_capacity;
    if (!ctx) {
        return 0;
    }
    if (ctx->match_count + additional <= ctx->match_capacity) {
        return 1;
    }
    new_capacity = ctx->match_capacity == 0 ? 256 : ctx->match_capacity;
    while (new_capacity < ctx->match_count + additional) {
        new_capacity *= 2;
    }
    matches = (PairMatchOutput *)realloc(ctx->matches, new_capacity * sizeof(PairMatchOutput));
    if (!matches) {
        return 0;
    }
    ctx->matches = matches;
    ctx->match_capacity = new_capacity;
    return 1;
}

static int append_pair_match(PairContext *ctx, uint32_t still_index, uint32_t motion_index, double confidence) {
    PairMatchOutput *match;
    if (!reserve_pair_matches(ctx, 1)) {
        return 0;
    }
    match = &ctx->matches[ctx->match_count];
    match->still_index = still_index;
    match->motion_index = motion_index;
    match->confidence = confidence;
    ctx->match_count++;
    return 1;
}

static int pair_row_is_photo(const PairRowEntry *row) {
    const char *suffix;
    if (!row || !row->rel) {
        return 0;
    }
    if (row->mime && str_case_starts_with(row->mime, "image/")) {
        return 1;
    }
    suffix = strrchr(row->rel, '.');
    if (!suffix) {
        return 0;
    }
    return
        strcasecmp(suffix, ".jpg") == 0 ||
        strcasecmp(suffix, ".jpeg") == 0 ||
        strcasecmp(suffix, ".png") == 0 ||
        strcasecmp(suffix, ".heic") == 0 ||
        strcasecmp(suffix, ".heif") == 0 ||
        strcasecmp(suffix, ".heifs") == 0 ||
        strcasecmp(suffix, ".heicf") == 0;
}

static int pair_row_is_video(const PairRowEntry *row) {
    const char *suffix;
    if (!row || !row->rel) {
        return 0;
    }
    if (row->content_id && row->content_id[0] != '\0' && !pair_row_is_photo(row)) {
        return 1;
    }
    if (row->mime && str_case_equals(row->mime, "video/quicktime")) {
        return 1;
    }
    suffix = strrchr(row->rel, '.');
    if (!suffix) {
        return 0;
    }
    return strcasecmp(suffix, ".mov") == 0 || strcasecmp(suffix, ".qt") == 0;
}

static double duration_score(double duration) {
    double midpoint;
    if (duration < LIVE_DURATION_PREFERRED_MIN) {
        return -LIVE_DURATION_PREFERRED_MIN + duration;
    }
    if (duration > LIVE_DURATION_PREFERRED_MAX) {
        return -duration;
    }
    midpoint = (LIVE_DURATION_PREFERRED_MIN + LIVE_DURATION_PREFERRED_MAX) / 2.0;
    return LIVE_DURATION_PREFERRED_MAX - fabs(midpoint - duration);
}

static int candidate_beats_best(const PairRowEntry *candidate, const PairRowEntry *best) {
    if (!best) {
        return 1;
    }
    if (candidate->has_dur && best->has_dur) {
        double current_score = duration_score(candidate->dur);
        double best_score = duration_score(best->dur);
        if (current_score > best_score) {
            return 1;
        }
        if (current_score < best_score) {
            return 0;
        }
    }
    if (candidate->has_still_image_time && !best->has_still_image_time) {
        return 1;
    }
    if (candidate->has_still_image_time && best->has_still_image_time) {
        if (
            candidate->still_image_time >= 0.0 &&
            (best->still_image_time < 0.0 || candidate->still_image_time < best->still_image_time)
        ) {
            return 1;
        }
    }
    return 0;
}

static int build_unique_indices(
    const PairContext *ctx,
    int want_photo,
    uint32_t **out_indices,
    size_t *out_count
) {
    uint32_t *indices;
    size_t count = 0;
    size_t index;

    if (!ctx || !out_indices || !out_count) {
        return 0;
    }

    indices = (uint32_t *)malloc(ctx->row_count * sizeof(uint32_t));
    if (!indices && ctx->row_count > 0) {
        return 0;
    }

    for (index = 0; index < ctx->row_count; index++) {
        const PairRowEntry *row = &ctx->rows[index];
        size_t existing;
        int include = want_photo ? row->is_photo : row->is_video;

        if (!include || !row->rel) {
            continue;
        }

        for (existing = 0; existing < count; existing++) {
            if (strcmp(ctx->rows[indices[existing]].rel, row->rel) == 0) {
                indices[existing] = (uint32_t)index;
                break;
            }
        }
        if (existing == count) {
            indices[count++] = (uint32_t)index;
        }
    }

    *out_indices = indices;
    *out_count = count;
    return 1;
}

static uint32_t find_best_content_id_match(
    const PairContext *ctx,
    const PairRowEntry *photo,
    const uint32_t *video_indices,
    size_t video_count,
    const unsigned char *used_videos
) {
    const PairRowEntry *best = NULL;
    uint32_t best_index = UINT32_MAX;
    size_t index;

    if (!photo || !photo->normalised_content_id) {
        return UINT32_MAX;
    }

    for (index = 0; index < video_count; index++) {
        uint32_t candidate_index = video_indices[index];
        const PairRowEntry *candidate = &ctx->rows[candidate_index];
        if (used_videos[candidate_index]) {
            continue;
        }
        if (
            !candidate->normalised_content_id ||
            strcmp(candidate->normalised_content_id, photo->normalised_content_id) != 0
        ) {
            continue;
        }
        if (candidate_beats_best(candidate, best)) {
            best = candidate;
            best_index = candidate_index;
        }
    }

    return best_index;
}

static uint32_t match_by_time(
    const PairContext *ctx,
    const PairRowEntry *photo,
    const uint32_t *video_indices,
    size_t video_count,
    const unsigned char *used_videos,
    int match_mode
) {
    size_t index;
    uint32_t best_index = UINT32_MAX;
    int64_t best_delta = 0;

    if (!photo || !photo->has_dt) {
        return UINT32_MAX;
    }

    for (index = 0; index < video_count; index++) {
        uint32_t candidate_index = video_indices[index];
        const PairRowEntry *candidate = &ctx->rows[candidate_index];
        int64_t delta;

        if (used_videos[candidate_index] || !candidate->has_dt) {
            continue;
        }
        if (match_mode == 0) {
            if (!photo->stem || !candidate->stem || strcmp(photo->stem, candidate->stem) != 0) {
                continue;
            }
        } else {
            if (!photo->folder || !candidate->folder || strcmp(photo->folder, candidate->folder) != 0) {
                continue;
            }
        }

        delta = photo->dt_us - candidate->dt_us;
        if (delta < 0) {
            delta = -delta;
        }
        if (delta > PAIR_TIME_DELTA_US) {
            continue;
        }
        if (best_index == UINT32_MAX || delta < best_delta) {
            best_index = candidate_index;
            best_delta = delta;
        }
    }

    return best_index;
}

static int build_pair_matches(PairContext *ctx) {
    uint32_t *photo_indices = NULL;
    uint32_t *video_indices = NULL;
    size_t photo_count = 0;
    size_t video_count = 0;
    unsigned char *used_videos = NULL;
    unsigned char *matched_photos = NULL;
    size_t index;

    if (!ctx) {
        return 0;
    }
    if (ctx->finalised) {
        return 1;
    }

    if (!build_unique_indices(ctx, 1, &photo_indices, &photo_count)) {
        goto error;
    }
    if (!build_unique_indices(ctx, 0, &video_indices, &video_count)) {
        goto error;
    }

    used_videos = (unsigned char *)calloc(ctx->row_count, sizeof(unsigned char));
    matched_photos = (unsigned char *)calloc(ctx->row_count, sizeof(unsigned char));
    if ((!used_videos && ctx->row_count > 0) || (!matched_photos && ctx->row_count > 0)) {
        goto error;
    }

    for (index = 0; index < photo_count; index++) {
        uint32_t photo_index = photo_indices[index];
        const PairRowEntry *photo = &ctx->rows[photo_index];
        uint32_t video_index = find_best_content_id_match(ctx, photo, video_indices, video_count, used_videos);
        if (video_index == UINT32_MAX) {
            continue;
        }
        used_videos[video_index] = 1;
        matched_photos[photo_index] = 1;
        if (!append_pair_match(ctx, photo_index, video_index, 1.0)) {
            goto error;
        }
    }

    for (index = 0; index < photo_count; index++) {
        uint32_t photo_index = photo_indices[index];
        const PairRowEntry *photo = &ctx->rows[photo_index];
        uint32_t video_index;
        if (matched_photos[photo_index]) {
            continue;
        }
        video_index = match_by_time(ctx, photo, video_indices, video_count, used_videos, 0);
        if (video_index == UINT32_MAX) {
            continue;
        }
        used_videos[video_index] = 1;
        matched_photos[photo_index] = 1;
        if (!append_pair_match(ctx, photo_index, video_index, 0.7)) {
            goto error;
        }
    }

    for (index = 0; index < photo_count; index++) {
        uint32_t photo_index = photo_indices[index];
        const PairRowEntry *photo = &ctx->rows[photo_index];
        uint32_t video_index;
        if (matched_photos[photo_index]) {
            continue;
        }
        video_index = match_by_time(ctx, photo, video_indices, video_count, used_videos, 1);
        if (video_index == UINT32_MAX) {
            continue;
        }
        used_videos[video_index] = 1;
        matched_photos[photo_index] = 1;
        if (!append_pair_match(ctx, photo_index, video_index, 0.5)) {
            goto error;
        }
    }

    ctx->finalised = 1;
    free(photo_indices);
    free(video_indices);
    free(used_videos);
    free(matched_photos);
    return 1;

error:
    free(photo_indices);
    free(video_indices);
    free(used_videos);
    free(matched_photos);
    return 0;
}

EXPORT void *pair_ctx_create_c(void) {
    return calloc(1, sizeof(PairContext));
}

EXPORT int pair_ctx_feed_rows_c(void *ctx_ptr, const PairRowInput *rows, size_t count) {
    PairContext *ctx = (PairContext *)ctx_ptr;
    size_t index;

    if (!ctx || (!rows && count > 0) || ctx->finalised) {
        return 0;
    }
    if (!reserve_pair_rows(ctx, count)) {
        return 0;
    }

    for (index = 0; index < count; index++) {
        PairRowEntry *entry = &ctx->rows[ctx->row_count];
        char normalised_buffer[512];
        int normalised_written = 0;

        memset(entry, 0, sizeof(*entry));
        normalised_buffer[0] = '\0';

        entry->rel = dup_string(rows[index].rel);
        entry->mime = dup_string(rows[index].mime);
        entry->dt = dup_string(rows[index].dt);
        entry->content_id = dup_string(rows[index].content_id);
        if (rows[index].content_id) {
            normalised_written = normalise_content_id(
                rows[index].content_id,
                normalised_buffer,
                sizeof(normalised_buffer)
            );
            if (normalised_written > 0) {
                entry->normalised_content_id = dup_string(normalised_buffer);
            }
        }
        if (entry->rel) {
            entry->stem = copy_stem(entry->rel);
            entry->folder = copy_folder(entry->rel);
        }
        if (
            (rows[index].rel && !entry->rel) ||
            (rows[index].mime && !entry->mime) ||
            (rows[index].dt && !entry->dt) ||
            (rows[index].content_id && !entry->content_id) ||
            (entry->rel && (!entry->stem || !entry->folder)) ||
            (normalised_written > 0 && !entry->normalised_content_id)
        ) {
            free_pair_row_entry(entry);
            return 0;
        }

        entry->dur = rows[index].dur;
        entry->still_image_time = rows[index].still_image_time;
        entry->has_dur = rows[index].has_dur ? 1 : 0;
        entry->has_still_image_time = rows[index].has_still_image_time ? 1 : 0;
        if (entry->dt && parse_iso8601_core(entry->dt, &entry->dt_us, NULL, NULL)) {
            entry->has_dt = 1;
        }
        entry->is_photo = pair_row_is_photo(entry);
        entry->is_video = pair_row_is_video(entry);
        ctx->row_count++;
    }

    return 1;
}

EXPORT int pair_ctx_finalize_next_chunk_c(
    void *ctx_ptr,
    size_t max_items,
    PairMatchOutput **out_matches,
    size_t *out_count,
    int *out_done
) {
    PairContext *ctx = (PairContext *)ctx_ptr;
    size_t remaining;
    size_t count;

    if (!ctx || !out_matches || !out_count || !out_done) {
        return 0;
    }
    if (!build_pair_matches(ctx)) {
        return 0;
    }
    if (max_items == 0) {
        max_items = 1;
    }

    remaining = ctx->match_count - ctx->match_cursor;
    if (remaining == 0) {
        *out_matches = NULL;
        *out_count = 0;
        *out_done = 1;
        return 1;
    }

    count = remaining < max_items ? remaining : max_items;
    *out_matches = ctx->matches + ctx->match_cursor;
    *out_count = count;
    ctx->match_cursor += count;
    *out_done = ctx->match_cursor >= ctx->match_count ? 1 : 0;
    return 1;
}

EXPORT void pair_ctx_destroy_c(void *ctx_ptr) {
    PairContext *ctx = (PairContext *)ctx_ptr;
    size_t index;
    if (!ctx) {
        return;
    }
    for (index = 0; index < ctx->row_count; index++) {
        free_pair_row_entry(&ctx->rows[index]);
    }
    free(ctx->rows);
    free(ctx->matches);
    free(ctx);
}

static int is_leap_year(int year) {
    if ((year % 400) == 0) {
        return 1;
    }
    if ((year % 100) == 0) {
        return 0;
    }
    return (year % 4) == 0;
}

static int days_in_month(int year, int month) {
    static const int DAYS[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (month < 1 || month > 12) {
        return 0;
    }
    if (month == 2 && is_leap_year(year)) {
        return 29;
    }
    return DAYS[month - 1];
}

static int read_n_digits(const char *value, int n) {
    int result = 0;
    int i;
    for (i = 0; i < n; i++) {
        if (value[i] < '0' || value[i] > '9') {
            return -1;
        }
        result = (result * 10) + (value[i] - '0');
    }
    return result;
}

static int parse_subsec_us(const char *value, const char **endp) {
    int digits = 0;
    int micros = 0;
    while (digits < 6 && value[digits] >= '0' && value[digits] <= '9') {
        micros = (micros * 10) + (value[digits] - '0');
        digits++;
    }
    while (digits < 6) {
        micros *= 10;
        digits++;
    }
    *endp = value + digits;
    while (**endp >= '0' && **endp <= '9') {
        (*endp)++;
    }
    return micros;
}

static int64_t tm_to_utc_epoch(struct tm *tm_value) {
#ifdef _WIN32
    return (int64_t)_mkgmtime(tm_value);
#else
    return (int64_t)timegm(tm_value);
#endif
}

static int parse_iso8601_core(
    const char *value,
    int64_t *unix_us,
    int *year_out,
    int *month_out
) {
    int year;
    int month;
    int day;
    int hour;
    int minute;
    int second;
    int micros = 0;
    int tz_offset_sec = 0;
    const char *cursor;
    struct tm tm_value;
    int64_t epoch;

    if (!value || strlen(value) < 19) {
        return 0;
    }

    year = read_n_digits(value, 4);
    month = read_n_digits(value + 5, 2);
    day = read_n_digits(value + 8, 2);
    hour = read_n_digits(value + 11, 2);
    minute = read_n_digits(value + 14, 2);
    second = read_n_digits(value + 17, 2);

    if (year < 0 || month < 1 || month > 12 || day < 1 || day > 31 ||
        hour < 0 || hour > 23 || minute < 0 || minute > 59 ||
        second < 0 || second > 60) {
        return 0;
    }
    if (day > days_in_month(year, month)) {
        return 0;
    }
    if (value[4] != '-' || value[7] != '-' || value[10] != 'T' ||
        value[13] != ':' || value[16] != ':') {
        return 0;
    }

    cursor = value + 19;
    if (*cursor == '.') {
        cursor++;
        micros = parse_subsec_us(cursor, &cursor);
    }

    if (*cursor == 'Z' || *cursor == 'z') {
        tz_offset_sec = 0;
        cursor++;
    } else if (*cursor == '+' || *cursor == '-') {
        int sign = (*cursor == '+') ? 1 : -1;
        int tz_hour;
        int tz_minute;
        cursor++;
        if (strlen(cursor) < 2) {
            return 0;
        }
        tz_hour = read_n_digits(cursor, 2);
        if (tz_hour < 0) {
            return 0;
        }
        cursor += 2;
        if (*cursor == ':') {
            cursor++;
        }
        if (strlen(cursor) < 2) {
            return 0;
        }
        tz_minute = read_n_digits(cursor, 2);
        if (tz_hour > 23 || tz_minute < 0 || tz_minute > 59) {
            return 0;
        }
        cursor += 2;
        tz_offset_sec = sign * ((tz_hour * 3600) + (tz_minute * 60));
    } else {
        return 0;
    }
    if (*cursor != '\0') {
        return 0;
    }

    memset(&tm_value, 0, sizeof(tm_value));
    tm_value.tm_year = year - 1900;
    tm_value.tm_mon = month - 1;
    tm_value.tm_mday = day;
    tm_value.tm_hour = hour;
    tm_value.tm_min = minute;
    tm_value.tm_sec = second;
    tm_value.tm_isdst = 0;

    epoch = tm_to_utc_epoch(&tm_value);
    if (unix_us) {
        *unix_us = (epoch - tz_offset_sec) * 1000000LL + micros;
    }
    if (year_out) {
        *year_out = year;
    }
    if (month_out) {
        *month_out = month;
    }
    return 1;
}

EXPORT int64_t parse_iso8601_to_unix_us(const char *value) {
    int64_t result = INT64_MIN;
    if (!parse_iso8601_core(value, &result, NULL, NULL)) {
        return INT64_MIN;
    }
    return result;
}

EXPORT int parse_iso8601_full(const char *value, int64_t *unix_us, int *year, int *month) {
    return parse_iso8601_core(value, unix_us, year, month);
}

static char *dup_string_len(const char *value, size_t length) {
    char *out = (char *)malloc(length + 1);
    if (!out) {
        return NULL;
    }
    memcpy(out, value, length);
    out[length] = '\0';
    return out;
}

static char *dup_string(const char *value) {
    if (!value) {
        return NULL;
    }
    return dup_string_len(value, strlen(value));
}

static int str_case_starts_with(const char *value, const char *prefix) {
    size_t index = 0;
    if (!value || !prefix) {
        return 0;
    }
    while (prefix[index] != '\0') {
        if (value[index] == '\0') {
            return 0;
        }
        if (tolower((unsigned char)value[index]) != tolower((unsigned char)prefix[index])) {
            return 0;
        }
        index++;
    }
    return 1;
}

static int str_case_equals(const char *left, const char *right) {
    if (!left || !right) {
        return 0;
    }
    while (*left && *right) {
        if (tolower((unsigned char)*left) != tolower((unsigned char)*right)) {
            return 0;
        }
        left++;
        right++;
    }
    return *left == '\0' && *right == '\0';
}

static const char *path_basename_posix(const char *path) {
    const char *slash;
    if (!path) {
        return NULL;
    }
    slash = strrchr(path, '/');
    return slash ? slash + 1 : path;
}

static char *copy_stem(const char *path) {
    const char *base;
    const char *dot;
    size_t length;
    if (!path) {
        return NULL;
    }
    base = path_basename_posix(path);
    dot = strrchr(base, '.');
    if (dot && dot != base) {
        length = (size_t)(dot - base);
    } else {
        length = strlen(base);
    }
    return dup_string_len(base, length);
}

static char *copy_folder(const char *path) {
    const char *slash;
    if (!path) {
        return NULL;
    }
    slash = strrchr(path, '/');
    if (!slash) {
        return dup_string(".");
    }
    return dup_string_len(path, (size_t)(slash - path));
}

static int glob_match_impl(const char *pattern, const char *text) {
    while (*pattern) {
        if (*pattern == '*') {
            while (*pattern == '*') {
                pattern++;
            }
            if (*pattern == '\0') {
                return 1;
            }
            while (*text) {
                if (glob_match_impl(pattern, text)) {
                    return 1;
                }
                text++;
            }
            return glob_match_impl(pattern, text);
        }
        if (*pattern == '?') {
            if (*text == '\0') {
                return 0;
            }
            pattern++;
            text++;
            continue;
        }
        if (*pattern != *text) {
            return 0;
        }
        pattern++;
        text++;
    }
    return *text == '\0';
}

static int matches_pattern(const char *pattern, const char *rel_path) {
    if (!pattern) {
        return 0;
    }
    if (glob_match_impl(pattern, rel_path)) {
        return 1;
    }
    if (strncmp(pattern, "**/", 3) == 0) {
        return glob_match_impl(pattern + 3, rel_path);
    }
    return 0;
}

EXPORT int should_include_c(
    const char *rel_path,
    const char **include_globs,
    size_t include_count,
    const char **exclude_globs,
    size_t exclude_count
) {
    size_t index;
    if (!rel_path) {
        return 0;
    }

    for (index = 0; index < exclude_count; index++) {
        if (matches_pattern(exclude_globs[index], rel_path)) {
            return 0;
        }
    }

    for (index = 0; index < include_count; index++) {
        if (matches_pattern(include_globs[index], rel_path)) {
            return 1;
        }
    }

    return include_count == 0 ? 1 : 0;
}

EXPORT int normalise_content_id(const char *value, char *out, size_t out_size) {
    const char *start;
    const char *end;
    size_t length;
    size_t index;

    if (!value || !out || out_size == 0) {
        return -1;
    }

    start = value;
    while (*start && isspace((unsigned char)*start)) {
        start++;
    }

    end = value + strlen(value);
    while (end > start && isspace((unsigned char)end[-1])) {
        end--;
    }

    length = (size_t)(end - start);
    if (length == 0) {
        out[0] = '\0';
        return 0;
    }
    if (length + 1 > out_size) {
        return -1;
    }

    for (index = 0; index < length; index++) {
        unsigned char c = (unsigned char)start[index];
        out[index] = (char)tolower(c);
    }
    out[length] = '\0';
    return (int)length;
}

static int file_seek(FILE *file, uint64_t offset) {
#ifdef _WIN32
    return _fseeki64(file, (int64_t)offset, SEEK_SET) == 0;
#else
    return fseeko(file, (off_t)offset, SEEK_SET) == 0;
#endif
}

static uint64_t file_tell(FILE *file) {
#ifdef _WIN32
    int64_t value = _ftelli64(file);
#else
    off_t value = ftello(file);
#endif
    if (value < 0) {
        return UINT64_MAX;
    }
    return (uint64_t)value;
}

static FILE *open_file_utf8(const char *path) {
#ifdef _WIN32
    int needed;
    wchar_t *wide_path;
    FILE *file;
    if (!path) {
        return NULL;
    }
    needed = MultiByteToWideChar(CP_UTF8, 0, path, -1, NULL, 0);
    if (needed <= 0) {
        return NULL;
    }
    wide_path = (wchar_t *)malloc((size_t)needed * sizeof(wchar_t));
    if (!wide_path) {
        return NULL;
    }
    if (MultiByteToWideChar(CP_UTF8, 0, path, -1, wide_path, needed) <= 0) {
        free(wide_path);
        return NULL;
    }
    file = _wfopen(wide_path, L"rb");
    free(wide_path);
    return file;
#else
    return fopen(path, "rb");
#endif
}

static int encode_hex(char *out, size_t out_size, XXH128_hash_t hash) {
    static const char HEX[] = "0123456789abcdef";
    XXH128_canonical_t canonical;
    size_t index;

    if (!out || out_size < 33) {
        return 0;
    }

    XXH128_canonicalFromHash(&canonical, hash);
    for (index = 0; index < 16; index++) {
        unsigned char byte = canonical.digest[index];
        out[index * 2] = HEX[(byte >> 4) & 0x0F];
        out[index * 2 + 1] = HEX[byte & 0x0F];
    }
    out[32] = '\0';
    return 1;
}

EXPORT int compute_file_id_c(const char *path, char *out_hex, size_t out_size) {
    FILE *file = NULL;
    uint64_t file_size = 0;
    XXH3_state_t *state = NULL;
    unsigned char *buffer = NULL;
    int ok = 0;

    if (!path || !out_hex || out_size < 33) {
        return 0;
    }

    file = open_file_utf8(path);
    if (!file) {
        return 0;
    }

    if (
#ifdef _WIN32
        _fseeki64(file, 0, SEEK_END) != 0
#else
        fseeko(file, 0, SEEK_END) != 0
#endif
    ) {
        goto cleanup;
    }
    file_size = file_tell(file);
    if (file_size == UINT64_MAX) {
        goto cleanup;
    }
    if (!file_seek(file, 0)) {
        goto cleanup;
    }

    state = XXH3_createState();
    if (!state) {
        goto cleanup;
    }
    if (XXH3_128bits_reset(state) != XXH_OK) {
        goto cleanup;
    }

    if (file_size <= HASH_THRESHOLD) {
        size_t chunk_size = (size_t)HASH_STREAM_CHUNK;
        buffer = (unsigned char *)malloc(chunk_size);
        if (!buffer) {
            goto cleanup;
        }
        while (!feof(file)) {
            size_t read_count = fread(buffer, 1, chunk_size, file);
            if (read_count > 0) {
                if (XXH3_128bits_update(state, buffer, read_count) != XXH_OK) {
                    goto cleanup;
                }
            }
            if (ferror(file)) {
                goto cleanup;
            }
        }
    } else {
        unsigned char size_le[8];
        uint64_t middle_offset;
        uint64_t tail_offset;
        size_t read_count;

        for (read_count = 0; read_count < 8; read_count++) {
            size_le[read_count] = (unsigned char)((file_size >> (read_count * 8)) & 0xFF);
        }
        if (XXH3_128bits_update(state, size_le, 8) != XXH_OK) {
            goto cleanup;
        }

        buffer = (unsigned char *)malloc((size_t)HASH_SAMPLE_SIZE);
        if (!buffer) {
            goto cleanup;
        }

        if (!file_seek(file, 0)) {
            goto cleanup;
        }
        read_count = fread(buffer, 1, (size_t)HASH_SAMPLE_SIZE, file);
        if (ferror(file)) {
            goto cleanup;
        }
        if (XXH3_128bits_update(state, buffer, read_count) != XXH_OK) {
            goto cleanup;
        }

        if (file_size > (HASH_SAMPLE_SIZE * 2ULL)) {
            middle_offset = (file_size / 2ULL) - (HASH_SAMPLE_SIZE / 2ULL);
            if (!file_seek(file, middle_offset)) {
                goto cleanup;
            }
            read_count = fread(buffer, 1, (size_t)HASH_SAMPLE_SIZE, file);
            if (ferror(file)) {
                goto cleanup;
            }
            if (XXH3_128bits_update(state, buffer, read_count) != XXH_OK) {
                goto cleanup;
            }
        }

        if (file_size > HASH_SAMPLE_SIZE) {
            tail_offset = file_size - HASH_SAMPLE_SIZE;
            if (!file_seek(file, tail_offset)) {
                goto cleanup;
            }
            read_count = fread(buffer, 1, (size_t)HASH_SAMPLE_SIZE, file);
            if (ferror(file)) {
                goto cleanup;
            }
            if (XXH3_128bits_update(state, buffer, read_count) != XXH_OK) {
                goto cleanup;
            }
        }
    }

    ok = encode_hex(out_hex, out_size, XXH3_128bits_digest(state));

cleanup:
    if (buffer) {
        free(buffer);
    }
    if (state) {
        XXH3_freeState(state);
    }
    if (file) {
        fclose(file);
    }
    return ok;
}

static int path_has_supported_extension(const char *path, const DiscoveryConfig *config) {
    const char *dot;
    size_t index;
    if (config->supported_count == 0) {
        return 1;
    }
    dot = strrchr(path, '.');
    if (!dot) {
        return 0;
    }
    for (index = 0; index < config->supported_count; index++) {
        if (strcasecmp(dot, config->supported_exts[index]) == 0) {
            return 1;
        }
    }
    return 0;
}

static int should_skip_dir_name(const char *name, const DiscoveryConfig *config) {
    size_t index;
    if (!name || !config) {
        return 0;
    }
    if (config->skip_hidden_dirs && name[0] == '.') {
        return 1;
    }
    for (index = 0; index < config->skip_dir_count; index++) {
        if (strcmp(name, config->skip_dir_names[index]) == 0) {
            return 1;
        }
    }
    return 0;
}

static int should_emit_rel_path(const char *rel_path, const DiscoveryConfig *config) {
    if (!path_has_supported_extension(rel_path, config)) {
        return 0;
    }
    if (config->include_count == 0 && config->exclude_count == 0) {
        return 1;
    }
    return should_include_c(
        rel_path,
        config->include_globs,
        config->include_count,
        config->exclude_globs,
        config->exclude_count
    );
}

static char *join_rel_path(const char *prefix, const char *name) {
    size_t prefix_len = prefix ? strlen(prefix) : 0;
    size_t name_len = name ? strlen(name) : 0;
    size_t total = prefix_len + name_len + 2;
    char *joined = (char *)malloc(total);
    if (!joined) {
        return NULL;
    }
    if (prefix_len == 0) {
        memcpy(joined, name, name_len);
        joined[name_len] = '\0';
        return joined;
    }
    memcpy(joined, prefix, prefix_len);
    joined[prefix_len] = '/';
    memcpy(joined + prefix_len + 1, name, name_len);
    joined[prefix_len + 1 + name_len] = '\0';
    return joined;
}

static int media_kind_from_rel_path(const char *rel_path) {
    const char *suffix = strrchr(rel_path, '.');
    if (!suffix) {
        return MEDIA_HINT_UNKNOWN;
    }
    if (
        strcasecmp(suffix, ".jpg") == 0 ||
        strcasecmp(suffix, ".jpeg") == 0 ||
        strcasecmp(suffix, ".png") == 0 ||
        strcasecmp(suffix, ".heic") == 0 ||
        strcasecmp(suffix, ".heif") == 0 ||
        strcasecmp(suffix, ".heifs") == 0 ||
        strcasecmp(suffix, ".heicf") == 0 ||
        strcasecmp(suffix, ".webp") == 0
    ) {
        return MEDIA_HINT_IMAGE;
    }
    if (
        strcasecmp(suffix, ".mov") == 0 ||
        strcasecmp(suffix, ".mp4") == 0 ||
        strcasecmp(suffix, ".m4v") == 0 ||
        strcasecmp(suffix, ".qt") == 0 ||
        strcasecmp(suffix, ".avi") == 0 ||
        strcasecmp(suffix, ".mkv") == 0
    ) {
        return MEDIA_HINT_VIDEO;
    }
    return MEDIA_HINT_UNKNOWN;
}

static int resolve_media_type(const char *rel_path, int media_hint) {
    if (media_hint == MEDIA_HINT_IMAGE) {
        return MEDIA_HINT_IMAGE;
    }
    if (media_hint == MEDIA_HINT_VIDEO) {
        return MEDIA_HINT_VIDEO;
    }
    if (!rel_path) {
        return MEDIA_HINT_UNKNOWN;
    }
    return media_kind_from_rel_path(rel_path);
}

static int append_discovery_item(DiscoveryHandle *handle, const char *abs_path, const char *rel_path) {
    DiscoveryItem *item;
    size_t new_capacity;
    if (!handle || !abs_path || !rel_path) {
        return 0;
    }
    if (handle->count == handle->capacity) {
        new_capacity = handle->capacity == 0 ? 256 : handle->capacity * 2;
        item = (DiscoveryItem *)realloc(handle->items, new_capacity * sizeof(DiscoveryItem));
        if (!item) {
            return 0;
        }
        handle->items = item;
        handle->capacity = new_capacity;
    }
    item = &handle->items[handle->count];
    memset(item, 0, sizeof(*item));
    item->abs_path = dup_string(abs_path);
    item->rel_path = dup_string(rel_path);
    if (!item->abs_path || !item->rel_path) {
        if (item->abs_path) {
            free(item->abs_path);
            item->abs_path = NULL;
        }
        if (item->rel_path) {
            free(item->rel_path);
            item->rel_path = NULL;
        }
        return 0;
    }
    item->media_kind = media_kind_from_rel_path(rel_path);
    handle->count++;
    return 1;
}

static int collect_discovery_callback(const char *path, const char *rel_path, void *userdata) {
    DiscoveryHandle *handle = (DiscoveryHandle *)userdata;
    if (!append_discovery_item(handle, path, rel_path)) {
        handle->failed = 1;
        return 1;
    }
    return 0;
}

#ifndef _WIN32
static char *join_fs_path(const char *prefix, const char *name) {
    size_t prefix_len = strlen(prefix);
    size_t name_len = strlen(name);
    char *joined = (char *)malloc(prefix_len + name_len + 2);
    if (!joined) {
        return NULL;
    }
    memcpy(joined, prefix, prefix_len);
    joined[prefix_len] = '/';
    memcpy(joined + prefix_len + 1, name, name_len);
    joined[prefix_len + 1 + name_len] = '\0';
    return joined;
}

static void discover_posix_recursive(
    const char *abs_dir,
    const char *rel_dir,
    DiscoveryConfig *config,
    int *count
) {
    DIR *dir;
    struct dirent *entry;

    dir = opendir(abs_dir);
    if (!dir) {
        return;
    }

    while (!config->stopped && (entry = readdir(dir)) != NULL) {
        struct stat st;
        char *abs_path;
        char *rel_path;

        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
            continue;
        }

        abs_path = join_fs_path(abs_dir, entry->d_name);
        if (!abs_path) {
            continue;
        }

        if (lstat(abs_path, &st) != 0) {
            free(abs_path);
            continue;
        }

        if (S_ISDIR(st.st_mode)) {
            if (!should_skip_dir_name(entry->d_name, config)) {
                rel_path = join_rel_path(rel_dir, entry->d_name);
                if (rel_path) {
                    discover_posix_recursive(abs_path, rel_path, config, count);
                    free(rel_path);
                }
            }
            free(abs_path);
            continue;
        }

        if (!S_ISREG(st.st_mode)) {
            free(abs_path);
            continue;
        }

        rel_path = join_rel_path(rel_dir, entry->d_name);
        if (!rel_path) {
            free(abs_path);
            continue;
        }

        if (should_emit_rel_path(rel_path, config)) {
            int stop = config->callback(abs_path, rel_path, config->userdata);
            if (stop) {
                config->stopped = 1;
            } else {
                (*count)++;
            }
        }

        free(rel_path);
        free(abs_path);
    }

    closedir(dir);
}
#else
static wchar_t *utf8_to_wide(const char *value) {
    int needed;
    wchar_t *out;
    if (!value) {
        return NULL;
    }
    needed = MultiByteToWideChar(CP_UTF8, 0, value, -1, NULL, 0);
    if (needed <= 0) {
        return NULL;
    }
    out = (wchar_t *)malloc((size_t)needed * sizeof(wchar_t));
    if (!out) {
        return NULL;
    }
    if (MultiByteToWideChar(CP_UTF8, 0, value, -1, out, needed) <= 0) {
        free(out);
        return NULL;
    }
    return out;
}

static char *wide_to_utf8(const wchar_t *value) {
    int needed;
    char *out;
    if (!value) {
        return NULL;
    }
    needed = WideCharToMultiByte(CP_UTF8, 0, value, -1, NULL, 0, NULL, NULL);
    if (needed <= 0) {
        return NULL;
    }
    out = (char *)malloc((size_t)needed);
    if (!out) {
        return NULL;
    }
    if (WideCharToMultiByte(CP_UTF8, 0, value, -1, out, needed, NULL, NULL) <= 0) {
        free(out);
        return NULL;
    }
    return out;
}

static wchar_t *join_wide_path(const wchar_t *prefix, const wchar_t *name) {
    size_t prefix_len = wcslen(prefix);
    size_t name_len = wcslen(name);
    wchar_t *joined = (wchar_t *)malloc((prefix_len + name_len + 2) * sizeof(wchar_t));
    if (!joined) {
        return NULL;
    }
    memcpy(joined, prefix, prefix_len * sizeof(wchar_t));
    joined[prefix_len] = L'\\';
    memcpy(joined + prefix_len + 1, name, name_len * sizeof(wchar_t));
    joined[prefix_len + 1 + name_len] = L'\0';
    return joined;
}

static void discover_windows_recursive(
    const wchar_t *abs_dir,
    const char *rel_dir,
    DiscoveryConfig *config,
    int *count
) {
    wchar_t *search = join_wide_path(abs_dir, L"*");
    WIN32_FIND_DATAW data;
    HANDLE handle;

    if (!search) {
        return;
    }

    handle = FindFirstFileW(search, &data);
    free(search);
    if (handle == INVALID_HANDLE_VALUE) {
        return;
    }

    do {
        const wchar_t *name_w = data.cFileName;
        char *name_utf8;

        if (wcscmp(name_w, L".") == 0 || wcscmp(name_w, L"..") == 0) {
            continue;
        }

        name_utf8 = wide_to_utf8(name_w);
        if (!name_utf8) {
            continue;
        }

        if ((data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
            if (
                !(data.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) &&
                !should_skip_dir_name(name_utf8, config)
            ) {
                wchar_t *child_abs = join_wide_path(abs_dir, name_w);
                char *child_rel = join_rel_path(rel_dir, name_utf8);
                if (child_abs && child_rel) {
                    discover_windows_recursive(child_abs, child_rel, config, count);
                }
                if (child_abs) {
                    free(child_abs);
                }
                if (child_rel) {
                    free(child_rel);
                }
            }
            free(name_utf8);
            continue;
        }

        {
            wchar_t *child_abs = join_wide_path(abs_dir, name_w);
            char *child_rel = join_rel_path(rel_dir, name_utf8);
            char *child_abs_utf8 = child_abs ? wide_to_utf8(child_abs) : NULL;

            if (child_rel && child_abs_utf8 && should_emit_rel_path(child_rel, config)) {
                int stop = config->callback(child_abs_utf8, child_rel, config->userdata);
                if (stop) {
                    config->stopped = 1;
                } else {
                    (*count)++;
                }
            }

            if (child_abs) {
                free(child_abs);
            }
            if (child_rel) {
                free(child_rel);
            }
            if (child_abs_utf8) {
                free(child_abs_utf8);
            }
        }

        free(name_utf8);
    } while (!config->stopped && FindNextFileW(handle, &data));

    FindClose(handle);
}
#endif
