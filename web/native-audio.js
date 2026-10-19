// Control the official PDF.js media annotation widgets. Never fetch/export their bytes.
export class NativeAudio {
  constructor(app, onChange, onEnded, rate = 1) {
    this.app = app; this.onChange = onChange; this.onEnded = onEnded;
    this.document = app.appConfig.mainContainer.ownerDocument;
    this.index = 0; this.disposed = false; this.rate = rate;
    this.events = event => {
      if (!event.target.matches?.('audio.mediaContent')) return;
      const records = this.records(), index = records.findIndex(el => el.contains(event.target));
      if (index < 0 || this.disposed) {
        if (event.type === 'play') event.target.pause();
        return;
      }
      if (event.type === 'play') {
        this.applyRate(event.target);
        event.target.controlsList?.add('nodownload');
        for (const media of this.document.querySelectorAll('audio.mediaContent')) {
          if (media !== event.target) media.pause();
        }
      }
      this.index = index; this.onChange(event.type);
      if (event.type === 'ended' && records.length === 1) this.onEnded();
    };
    for (const event of ['play', 'pause', 'ended', 'error']) this.document.addEventListener(event, this.events, true);
    this.rendered = () => { if (!this.disposed) this.onChange('rendered'); };
    app.eventBus.on('annotationlayerrendered', this.rendered);
  }
  records() {
    return [...(this.app.pdfViewer?.getPageView(this.app.page - 1)?.div
      .querySelectorAll('.mediaAnnotation') || [])].filter(el => el.querySelector('.mediaPlayButton,audio.mediaContent'));
  }
  media() { return this.records()[this.index]?.querySelector('audio.mediaContent'); }
  applyRate(media) {
    media.defaultPlaybackRate = this.rate;
    media.playbackRate = this.rate;
    media.preservesPitch = true;
  }
  setRate(rate) {
    this.rate = rate;
    for (const media of this.document.querySelectorAll('audio.mediaContent')) this.applyRate(media);
  }
  pause(reset = false) {
    for (const media of this.document.querySelectorAll('audio.mediaContent')) {
      media.pause();
      if (reset && media.readyState) media.currentTime = 0;
    }
  }
  async waitFor(find, page) {
    const started = performance.now();
    while (!this.disposed && this.app.page === page && performance.now() - started < 10000) {
      const value = find(); if (value) return value;
      await new Promise(resolve => setTimeout(resolve, 30));
    }
    throw new Error('Native audio unavailable or page changed');
  }
  async play(index = this.index, restart = false) {
    const page = this.app.page;
    const record = await this.waitFor(() => this.records()[index], page);
    this.index = index;
    if (!record.querySelector('audio.mediaContent')) record.querySelector('.mediaPlayButton')?.click();
    const media = await this.waitFor(() => record.querySelector('audio.mediaContent'), page);
    this.applyRate(media);
    if (restart && media.readyState) media.currentTime = 0;
    await media.play();
  }
  destroy() {
    this.disposed = true; this.pause();
    for (const event of ['play', 'pause', 'ended', 'error']) this.document.removeEventListener(event, this.events, true);
    this.app.eventBus.off('annotationlayerrendered', this.rendered);
  }
}
