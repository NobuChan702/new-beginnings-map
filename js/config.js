// Zoom for the New Beginnings map.
// Zoom 0 shows the whole world. Native resolution is native_zoom in
// data/meta.json (6 for this sheet). The source image is 12000×5600 at
// 5.5 km per pixel, so the raster cannot get any sharper than that.
// overzoomLevels adds close-up steps past that point. Auto, Political, and
// Depth become a flat board there. Terrain keeps the terrain tiles.
// Reload the page after editing. A rebuild is not required for these numbers.
window.NB_CONFIG = {
  minZoom: 0,
  overzoomLevels: 3,

  // Political map stays solid until this zoom, then fades toward a tint.
  politicalOpaqueUntil: 2,
  politicalFadeEnd: 4.25,
  // Opacity of the political layer once the fade finishes (terrain shows through).
  politicalTintOpacity: 0.28,

  // Province borders on the photo fade in across this zoom range.
  // This finishes before native zoom, while the picture is still sharp.
  outlinesFadeStart: 2.75,
  outlinesFadeEnd: 4.5,
  outlineOpacity: 0.9,

  // Layers → Province IDs draws numbers from this zoom through the closest flat board.
  provinceLabelMinZoom: 5,

  // Added to native_zoom. The photo fades out and the flat board fades in
  // across this span (default: solid one level past native).
  flatFadeStartOffset: 0,
  flatFadeEndOffset: 1,
};
