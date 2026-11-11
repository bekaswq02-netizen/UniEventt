/* UniEvent: камера арқылы Face ID. Кітапхана: https://github.com/vladmandic/face-api (модельдер локальді). */
let faceModelsPromise = null;
function loadFaceModels() {
  if (!window.faceapi) return Promise.reject(new Error('Face API кітапханасы жүктелмеді'));
  if (!faceModelsPromise) {
    faceModelsPromise = Promise.all([
      faceapi.nets.tinyFaceDetector.loadFromUri('/face-api/model'),
      faceapi.nets.faceLandmark68Net.loadFromUri('/face-api/model'),
      faceapi.nets.faceRecognitionNet.loadFromUri('/face-api/model'),
    ]).catch(err => { faceModelsPromise = null; throw err; });
  }
  return faceModelsPromise;
}

function averageDescriptor(list) {
  const mean = new Array(128).fill(0);
  for (const d of list) for (let i = 0; i < 128; i++) mean[i] += d[i];
  for (let i = 0; i < 128; i++) mean[i] /= list.length;
  const norm = Math.sqrt(mean.reduce((s, v) => s + v * v, 0)) || 1;
  return mean.map(v => v / norm);
}

/* Камера терезесі: samples кадр жинап, орташа дескрипторды onSubmit-ке береді.
   onSubmit қате лақтырса — қайта жинау (3 әрекетке дейін). */
function faceCapture({ title, note, samples = 5, onSubmit }) {
  return new Promise((resolve, reject) => {
    const panel = document.createElement('div');
    panel.className = 'fixed inset-0 bg-slate-950/90 z-[210] flex items-center justify-center p-4';
    const card = document.createElement('div');
    card.className = 'bg-white rounded-2xl shadow-xl p-6 w-full max-w-md text-sm space-y-3';
    const heading = document.createElement('h2'); heading.className = 'font-bold text-lg'; heading.textContent = title;
    const hint = document.createElement('p'); hint.className = 'text-slate-600 text-xs'; hint.textContent = note;
    const stage = document.createElement('div'); stage.className = 'relative rounded-xl overflow-hidden bg-slate-900';
    const video = document.createElement('video');
    video.autoplay = true; video.playsInline = true; video.muted = true;
    video.className = 'w-full block'; video.style.transform = 'scaleX(-1)';
    const canvas = document.createElement('canvas');
    canvas.className = 'absolute inset-0 w-full h-full'; canvas.style.transform = 'scaleX(-1)';
    stage.append(video, canvas);
    const status = document.createElement('p'); status.className = 'text-xs text-slate-500'; status.textContent = 'Модельдер жүктелуде...';
    const error = document.createElement('p'); error.className = 'text-red-600 text-xs';
    const cancel = document.createElement('button');
    cancel.type = 'button'; cancel.className = 'w-full text-slate-500 underline'; cancel.textContent = 'Бас тарту';
    card.append(heading, hint, stage, status, error, cancel);
    panel.append(card); document.body.append(panel);

    let stream = null, timer = null, busy = false, done = false, attempts = 0;
    const collected = [];

    function cleanup() {
      done = true;
      if (timer) { clearInterval(timer); timer = null; }
      if (stream) stream.getTracks().forEach(t => t.stop());
      panel.remove();
    }
    cancel.onclick = () => { cleanup(); const e = new Error('Бас тартылды'); e.cancelled = true; reject(e); };

    async function tick() {
      if (busy || done) return;
      busy = true;
      try {
        const det = await faceapi.detectSingleFace(video, new faceapi.TinyFaceDetectorOptions({ inputSize: 320, scoreThreshold: 0.5 }))
          .withFaceLandmarks().withFaceDescriptor();
        if (done) return;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (!det || det.detection.box.width < 80) {
          status.textContent = 'Бет табылмады. Камераға тік қараңыз, жарық жеткілікті болсын.';
          return;
        }
        const box = det.detection.box;
        ctx.strokeStyle = '#22c55e'; ctx.lineWidth = 3;
        ctx.strokeRect(box.x, box.y, box.width, box.height);
        collected.push(Array.from(det.descriptor));
        status.textContent = `Үлгілер: ${collected.length}/${samples}`;
        if (collected.length >= samples) {
          clearInterval(timer); timer = null;
          status.textContent = 'Тексерілуде...';
          try {
            const result = await onSubmit(averageDescriptor(collected));
            cleanup(); resolve(result);
          } catch (err) {
            attempts++;
            collected.length = 0;
            if (attempts >= 3) { cleanup(); reject(err); return; }
            error.textContent = err.message || 'Бет сәйкес келмеді';
            status.textContent = 'Қайта көріңіз...';
            timer = setInterval(tick, 300);
          }
        }
      } catch (_) { /* кадр өңдеу қатесі — келесі күтеміз */ }
      finally { busy = false; }
    }

    (async () => {
      try {
        await loadFaceModels();
        if (done) return;
        status.textContent = 'Камераға рұқсат беріңіз...';
        stream = await navigator.mediaDevices.getUserMedia({
          audio: false,
          video: { facingMode: 'user', width: { ideal: 640 }, height: { ideal: 480 } },
        });
        if (done) { stream.getTracks().forEach(t => t.stop()); return; }
        video.srcObject = stream;
        await new Promise(r => { video.onloadedmetadata = r; });
        await video.play();
        canvas.width = video.videoWidth; canvas.height = video.videoHeight;
        status.textContent = 'Бетіңізді камераға қаратыңыз...';
        timer = setInterval(tick, 300);
      } catch (err) {
        cleanup();
        const message = err.name === 'NotAllowedError' ? 'Камераға рұқсат берілмеді. Браузер баптауларынан рұқсат етіңіз.'
          : err.name === 'NotFoundError' ? 'Камера табылмады'
          : (err.message || 'Камера қосылмады');
        reject(new Error(message));
      }
    })();
  });
}

async function showFaceSetup(challenge) {
  const result = await faceCapture({
    title: 'Face ID (камера) орнату',
    note: 'Камераға тік қараңыз, бетіңіз толық көрінсін. 5 кадр жиналып, бет үлгісі сақталады.',
    samples: 5,
    onSubmit: descriptor => api('/face/enroll', { method: 'POST', body: JSON.stringify({ ticket: challenge.ticket, descriptor }) }),
  });
  applySession(result); await reloadEvents(); showToast('Face ID (камера) қосылды!');
}

async function showFaceVerify(challenge) {
  const result = await faceCapture({
    title: 'Face ID арқылы кіру',
    note: 'Камераға қараңыз — бетіңіз сақталған үлгімен салыстырылады.',
    samples: 3,
    onSubmit: descriptor => api('/face/verify', { method: 'POST', body: JSON.stringify({ ticket: challenge.ticket, descriptor }) }),
  });
  applySession(result); await reloadEvents(); showToast('Face ID арқылы сәтті кірдіңіз!');
}