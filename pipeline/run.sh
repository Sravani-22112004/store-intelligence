#!/bin/bash
# run.sh — Process all 5 CCTV clips and stream events to the API

set -e

CLIPS_DIR="${1:-../data/clips/CCTV Footage}"
LAYOUT="${2:-../data/store_layout.json}"
API_URL="${3:-http://localhost:8000}"
OUTPUT_DIR="../data/events"
STORE_ID="STORE_BLR_002"
CLIP_START="2026-04-10T10:00:00"

mkdir -p "$OUTPUT_DIR"

echo "=== Store Intelligence Detection Pipeline ==="
echo "Clips dir : $CLIPS_DIR"
echo "Layout    : $LAYOUT"
echo "API       : $API_URL"
echo ""

declare -A CAM_IDS=(
  ["CAM 1.mp4"]="CAM_1"
  ["CAM 2.mp4"]="CAM_2"
  ["CAM 3.mp4"]="CAM_3"
  ["CAM 4.mp4"]="CAM_4"
  ["CAM 5.mp4"]="CAM_5"
)

for clip_name in "CAM 1.mp4" "CAM 2.mp4" "CAM 3.mp4" "CAM 4.mp4" "CAM 5.mp4"; do
  clip="$CLIPS_DIR/$clip_name"
  if [ ! -f "$clip" ]; then
    echo "⚠️  Not found: $clip — skipping"
    continue
  fi

  cam_id="${CAM_IDS[$clip_name]}"
  safe_name="${clip_name// /_}"
  output="$OUTPUT_DIR/${safe_name%.mp4}.jsonl"

  echo "▶  Processing: $clip_name → $cam_id"

  python detect.py \
    --video "$clip" \
    --store-id "$STORE_ID" \
    --camera-id "$cam_id" \
    --layout "$LAYOUT" \
    --output "$output" \
    --api-url "$API_URL" \
    --clip-start "$CLIP_START"

  echo "   ✅ Done → $output"
  echo ""
done

echo "=== All clips processed ==="
echo "Events saved in: $OUTPUT_DIR"
