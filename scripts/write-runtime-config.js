const apiBaseUrl = process.env.API_BASE_URL;

if (!apiBaseUrl) {
  throw new Error("Set API_BASE_URL in the Amplify build environment.");
}

process.stdout.write(
  `window.APP_CONFIG = ${JSON.stringify({ apiBaseUrl })};\n`
);
