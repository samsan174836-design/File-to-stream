if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
        navigator.serviceWorker.register("/service-worker.js", { scope: "/" })
            .catch((error) => console.error("App installation support could not be enabled.", error));
    });
}
