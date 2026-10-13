document.getElementById("print-document").addEventListener("click", () => window.print());
document.querySelectorAll("[data-qr]").forEach((element) => {
  new QRCode(element, {text: element.dataset.qr, width: 144, height: 144, correctLevel: QRCode.CorrectLevel.M});
});
