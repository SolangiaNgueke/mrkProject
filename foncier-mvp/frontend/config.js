// ------------------------------------------------------------------
//  Configuration du frontend — SEUL fichier à modifier au déploiement
// ------------------------------------------------------------------

// Adresse de l'API — détectée automatiquement :
//   - ouverte depuis ta machine  -> API locale (Docker)
//   - ouverte depuis Internet    -> API de production
// Un seul endroit à modifier si l'adresse de production change.
window.API_PROD = "https://mylandsure.onrender.com/api/";

(function () {
  const local = ["localhost", "127.0.0.1", ""].includes(location.hostname);
  window.API_ROOT = local ? "http://localhost:8000/api/" : window.API_PROD;
})();

// Clé MapTiler (fonds de carte haute qualité, plan + satellite).
// Laisser vide -> repli automatique sur OpenFreeMap + Esri (moins net).
// À restreindre par domaine depuis le tableau de bord MapTiler.
window.MAPTILER_KEY = "3VtiMKqdlpqOeW5Lnv4u";

// --- Styles de carte (calculés à partir de la clé) ---
(function () {
  const k = window.MAPTILER_KEY;
  const actif = k && k !== "3VtiMKqdlpqOeW5Lnv4u";

  // Fond « plan » : rues et bâtiments, style clair à la Zillow.
  window.STYLE_PLAN = actif
    ? `https://api.maptiler.com/maps/streets-v2/style.json?key=${k}`
    : "https://tiles.openfreemap.org/styles/liberty";

  // Fond « satellite » : imagerie aérienne. Le style "hybrid" de MapTiler
  // superpose les noms de rues à la photo, ce qui aide à se repérer.
  window.STYLE_SAT = actif
    ? `https://api.maptiler.com/maps/hybrid/style.json?key=${k}`
    : {
        version: 8,
        sources: { sat: { type: "raster",
          tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
          tileSize: 256, attribution: "Imagery © Esri" } },
        layers: [{ id: "sat", type: "raster", source: "sat" }]
      };
})();