// Zoom crossfade for the New Beginnings map.
// Zoom 0 shows the whole world. Zoom 6 is native pixel resolution
// (that number comes from the build and is also in data/meta.json).
// Zooms above native overscale the tiles. Reload the page after editing.
window.NB_CONFIG = {
  minZoom: 0,
  maxZoom: 8,

  // Political map stays solid until this zoom, then fades toward a tint.
  politicalOpaqueUntil: 2,
  politicalFadeEnd: 4.25,
  // Opacity of the political layer once the fade finishes (terrain shows through).
  politicalTintOpacity: 0.28,

  // Province borders fade in across this zoom range.
  outlinesFadeStart: 2.75,
  outlinesFadeEnd: 4.5,
  outlineOpacity: 0.9,

  // Layers → Province IDs draws numbers only at this zoom and closer.
  provinceLabelMinZoom: 5,
};
