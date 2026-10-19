// Presentation bands only: never change the model's stored score.
export const assessmentPassScore = 80;
export const rewardTiers = [
  [95, 5, 'Amazing!'], [90, 4.5, 'Excellent!'], [80, 4, 'Perfect!'],
  [70, 3.5, 'Great!'], [60, 3, 'Good job!'], [50, 2.5, 'Good try!'],
  [40, 2, "Let's try again!"], [30, 1.5, "Let's try again!"], [20, 1, "Let's try again!"],
  [1, .5, "Let's try again!"], [0, 0, "Let's try again!"],
];
// Populate only with checked spoken phrases. Never substitute crowd cheering.
// Asset basename under /assets/rewards; keep the spoken phrase equal to its key.
let voices;
export function loadRewardVoices() {
  voices ||= fetch('/assets/rewards/voice-manifest.json').then(r => r.ok ? r.json() : {})
    .then(data => Object.fromEntries(Object.entries(data.voices || {})
      .filter(([text, file]) => rewardTiers.some(t => t[2] === text) && /^volc-[a-z0-9-]+$/.test(file))))
    .catch(() => ({}));
  return voices;
}
export function rewardTier(score) {
  return Number.isFinite(score) && score >= 0 && score <= 100
    ? rewardTiers.find(([minimum]) => score >= minimum) : null;
}
export function rewardStars(count) {
  return Array.from({ length: 5 }, (_, index) => {
    const star = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    star.setAttribute('viewBox', '0 0 24 24'); star.setAttribute('aria-hidden', 'true');
    star.classList.add('reward-star');
    star.dataset.fill = String(Math.max(0, Math.min(1, count - index)));
    for (const role of ['empty', 'filled']) {
      const shape = document.createElementNS(star.namespaceURI, 'path');
      shape.setAttribute('d', 'M12 2.5l2.85 5.78 6.38.93-4.62 4.5 1.09 6.35L12 17.06l-5.7 3 1.09-6.35-4.62-4.5 6.38-.93z');
      shape.setAttribute('class', role); star.append(shape);
    }
    return star;
  });
}
