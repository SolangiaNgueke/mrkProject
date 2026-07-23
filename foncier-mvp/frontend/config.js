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
window.MAPTILER_KEY = "COLLE_TA_CLE_ICI";

// --- Styles de carte (calculés à partir de la clé) ---
(function () {
  const k = window.MAPTILER_KEY;
  const actif = k && k !== "COLLE_TA_CLE_ICI";

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

// ------------------------------------------------------------------
//  Anti double-clic
// ------------------------------------------------------------------
// Empêche les clics répétés inutiles : le bouton se verrouille pendant
// l'action, puis reste inactif un court instant (délai de garde).
// Évite notamment de créer deux fois la même parcelle.

window.DELAI_GARDE_MS = 1500;   // pause après une action terminée
window.DELAI_CLIC_CARTE_MS = 500;  // pause entre deux clics sur la carte

window.actionProtegee = async function (bouton, action, delaiGarde) {
  if (!bouton || bouton.dataset.enCours === "1") return;
  const garde = delaiGarde || window.DELAI_GARDE_MS;

  bouton.dataset.enCours = "1";
  const texteInitial = bouton.textContent;
  const largeur = bouton.offsetWidth;      // évite que le bouton rétrécisse
  bouton.style.minWidth = largeur + "px";
  bouton.disabled = true;
  bouton.style.opacity = "0.6";
  bouton.style.cursor = "progress";
  bouton.textContent = "Veuillez patienter…";

  try {
    await action();
  } finally {
    setTimeout(function () {
      bouton.disabled = false;
      bouton.style.opacity = "";
      bouton.style.cursor = "";
      bouton.style.minWidth = "";
      bouton.textContent = texteInitial;
      delete bouton.dataset.enCours;
    }, garde);
  }
};

// Limite la fréquence d'une action déclenchée par des clics rapprochés
// (utilisée pour le placement du point sur la carte).
window.limiterFrequence = function (fn, delai) {
  let dernier = 0;
  return function () {
    const maintenant = Date.now();
    if (maintenant - dernier < (delai || window.DELAI_CLIC_CARTE_MS)) return;
    dernier = maintenant;
    return fn.apply(this, arguments);
  };
};