window.uploadPictureToS3 = async function (file, startPath, completePath, authorization) {
  const apiBaseUrl = (window.APP_CONFIG?.apiBaseUrl || "").replace(/\/$/, "");
  const apiHeaders = { "Content-Type": "application/json" };
  if (authorization) apiHeaders.Authorization = authorization;

  const startResponse = await fetch(`${apiBaseUrl}${startPath}`, {
    method: "POST",
    headers: apiHeaders,
    body: JSON.stringify({
      filename: file.name,
      contentType: file.type,
      size: file.size
    }),
    cache: "no-store"
  });
  const upload = await startResponse.json();
  if (!startResponse.ok) throw new Error(upload.error || "Could not prepare the upload.");

  const formData = new FormData();
  for (const [name, value] of Object.entries(upload.fields)) {
    formData.append(name, value);
  }
  formData.append("file", file);

  const storageResponse = await fetch(upload.url, {
    method: "POST",
    body: formData
  });
  if (!storageResponse.ok) throw new Error("The picture could not be uploaded to storage.");

  const completeResponse = await fetch(`${apiBaseUrl}${completePath}`, {
    method: "POST",
    headers: apiHeaders,
    body: JSON.stringify({ uploadId: upload.uploadId }),
    cache: "no-store"
  });
  const result = await completeResponse.json();
  if (!completeResponse.ok) throw new Error(result.error || "Could not finish the upload.");
  return result;
};
