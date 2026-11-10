/* 逃走中のBGM。音楽ファイルは使わず、Web Audio で鳴らす（著作権の確認が不要）。
   ZombieBGM.unlock() はタップの中で呼ぶ（スマホのブラウザは、触る前は音を出せない）。
   ZombieBGM.play() / stop() で鳴らし、オン・オフは setEnabled() で切り替える（端末ごとに覚える）。 */
(function () {
  var STORAGE_KEY = "zombie-bgm-enabled";
  var TEMPO = 132;  // 心臓の鼓動に合わせる。不気味さを出すため、刻みは減らしてゆっくりめにした
  var STEPS_PER_BAR = 16;
  var LOOKAHEAD_S = 0.12;
  var TICK_MS = 25;
  var VOLUME = 0.22;

  /* ホラーゲームの「何かが近くにいる」感じを目指す。派手なドラムや警報はやめ、すき間の多い音にした
     - ドラムの代わりに、低い心臓の鼓動（ドクン、ドクン）
     - 半音でぶつかる和音（Am → B♭ → Am → E7(♭9)）と、半音と増4度が重なる低いうなり
     - こもった低い刻み（オスティナート）。4小節かけて少しずつ開き、じわじわ迫ってくる
     - 少し音程の狂ったオルゴールを、こだま付きで遠くに鳴らす
     - 弦をこするような高い音が、ゆっくり半音ずり落ちる
     - ときどき、ささやき声のような雑音、遠くで鉄を打つ音が混ざる（鳴る時刻は少しずつずらす）
     ゾンビらしさ
     - 母音の響きを付けた、がらがらのうめき声。近くで毎ループ鳴り、高さと母音を毎回変える
     - 遠くで群れがうめく声（2ループに1回、こもらせて重ねる）
     - 足を引きずって歩く音
     数字は A=0 からの半音 */
  var CHORDS = [
    { root: 0, notes: [0, 3, 7] },
    { root: 0, notes: [0, 3, 7] },
    { root: 1, notes: [1, 5, 8] },
    { root: -5, notes: [-5, -1, 2, 6] },
  ];
  var LOOP_BARS = CHORDS.length;
  var OSTINATO = [0, 0, 12, 0, 0, 13, 0, 12, 0, 0, 12, 0, 13, 0, 12, 7];  // 半音上（13）でぶつける
  var HEARTBEAT = [0, 3, 8, 11];  // ドクン（強）・ドクン（弱）を半小節ごとに
  // オルゴール。2小節で1フレーズ（32ステップ）。短調から半音でずれていく
  var MUSIC_BOX = [
    24, null, null, 27, null, null, 31, null, 30, null, null, null, 27, null, null, null,
    24, null, null, 27, null, null, 26, null, 25, null, null, null, null, null, null, null,
  ];

  var ctx = null;
  var master = null;
  var echo = null;  // こだま（ディレイ）の入口
  var fx = null;  // 効果音。BGMを止めても鳴らせるよう、BGMとは別の出口にする
  var noise = null;
  var timer = null;
  var playing = false;
  var threat = 0;  // 近づかれた怖さ（0〜1）。息づかい、叫び声、心臓の速さに使う
  var nextAt = 0;
  var step = 0;

  // iPhone はマナーモードのとき、Web Audio の音を消す。<audio> を鳴らしている間は消さないので、
  // 無音の短い WAV をくり返し鳴らしておく。新しい Safari では audioSession で「再生」の扱いにもする
  var silence = null;
  function silentWav() {
    var rate = 8000;
    var samples = rate / 2;
    var bytes = new Uint8Array(44 + samples);
    var view = new DataView(bytes.buffer);
    function text(offset, value) { for (var i = 0; i < value.length; i += 1) { bytes[offset + i] = value.charCodeAt(i); } }
    text(0, "RIFF"); view.setUint32(4, 36 + samples, true); text(8, "WAVE");
    text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, rate, true); view.setUint32(28, rate, true); view.setUint16(32, 1, true); view.setUint16(34, 8, true);
    text(36, "data"); view.setUint32(40, samples, true);
    for (var j = 44; j < bytes.length; j += 1) { bytes[j] = 128; }  // 8bit の無音
    var binary = "";
    for (var k = 0; k < bytes.length; k += 1) { binary += String.fromCharCode(bytes[k]); }
    return "data:audio/wav;base64," + btoa(binary);
  }
  function keepSessionPlaying() {
    try {
      if (navigator.audioSession) { navigator.audioSession.type = "playback"; }
    } catch (e) { /* 対応していないブラウザでは何もしない */ }
    if (!silence) {
      silence = document.createElement("audio");
      silence.src = silentWav();
      silence.loop = true;
      silence.setAttribute("playsinline", "");
    }
    var played = silence.play();
    if (played && played.catch) { played.catch(function () {}); }
  }

  function enabled() {
    try { return localStorage.getItem(STORAGE_KEY) !== "off"; } catch (e) { return true; }
  }

  function freq(semitone, octave) {
    return 220 * Math.pow(2, semitone / 12 + octave);  // A3 = 220Hz を基準にする
  }

  function setUp() {
    if (ctx) { return true; }
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) { return false; }
    ctx = new Ctx();
    master = ctx.createGain();
    master.gain.value = VOLUME;
    var compressor = ctx.createDynamicsCompressor();
    master.connect(compressor);
    compressor.connect(ctx.destination);
    fx = ctx.createGain();
    fx.gain.value = 0.5;
    fx.connect(compressor);
    // こだま。付点8分で返し、少しずつこもらせながら消えていく
    echo = ctx.createGain();
    var delay = ctx.createDelay(1);
    delay.delayTime.value = (60 / TEMPO) * 0.75;
    var feedback = ctx.createGain();
    feedback.gain.value = 0.45;
    var dark = ctx.createBiquadFilter();
    dark.type = "lowpass";
    dark.frequency.value = 2200;
    echo.connect(delay);
    delay.connect(dark);
    dark.connect(feedback);
    feedback.connect(delay);
    dark.connect(master);
    // ハイハットとスネアに使う白色雑音
    noise = ctx.createBuffer(1, ctx.sampleRate, ctx.sampleRate);
    var data = noise.getChannelData(0);
    for (var i = 0; i < data.length; i += 1) { data[i] = Math.random() * 2 - 1; }
    return true;
  }

  function envelope(gain, at, peak, attack, release) {
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(peak, at + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + attack + release);
  }

  function tone(type, hz, at, length, peak, filterHz) {
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(hz, at);
    var last = osc;
    if (filterHz) {
      var filter = ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.setValueAtTime(filterHz, at);
      osc.connect(filter);
      last = filter;
    }
    last.connect(gain);
    gain.connect(master);
    envelope(gain, at, peak, 0.01, length);
    osc.start(at);
    osc.stop(at + length + 0.05);
  }

  function kick(at) {
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.frequency.setValueAtTime(140, at);
    osc.frequency.exponentialRampToValueAtTime(45, at + 0.12);
    osc.connect(gain);
    gain.connect(master);
    envelope(gain, at, 0.9, 0.005, 0.22);
    osc.start(at);
    osc.stop(at + 0.3);
  }

  function hit(at, filterType, filterHz, peak, length) {
    var source = ctx.createBufferSource();
    source.buffer = noise;
    var filter = ctx.createBiquadFilter();
    filter.type = filterType;
    filter.frequency.value = filterHz;
    var gain = ctx.createGain();
    source.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    envelope(gain, at, peak, 0.002, length);
    source.start(at);
    source.stop(at + length + 0.05);
  }

  function pad(chord, at, length) {
    chord.notes.forEach(function (note) {
      [-4, 4].forEach(function (detune) {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.type = "triangle";
        osc.frequency.value = freq(note, 0);
        osc.detune.value = detune;
        osc.connect(gain);
        gain.connect(master);
        gain.gain.setValueAtTime(0.0001, at);
        gain.gain.exponentialRampToValueAtTime(0.05, at + 0.3);
        gain.gain.setValueAtTime(0.05, at + length - 0.2);
        gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
        osc.start(at);
        osc.stop(at + length + 0.05);
      });
    });
  }

  /* ループの最後の小節で、雑音のフィルターを開いていき、頭で一気に止める */
  function riser(at, length) {
    var source = ctx.createBufferSource();
    source.buffer = noise;
    source.loop = true;
    var filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.Q.value = 6;
    filter.frequency.setValueAtTime(400, at);
    filter.frequency.exponentialRampToValueAtTime(6000, at + length);
    var gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.18, at + length);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + length + 0.05);
    source.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    source.start(at);
    source.stop(at + length + 0.1);
  }

  /* オルゴールの1音。音程を少し狂わせ、こだまに多めに送って遠くで鳴っているように聞かせる */
  function musicBox(semitone, at) {
    [0, 1200].forEach(function (octave, i) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq(semitone, 0);
      osc.detune.value = octave - 18 + Math.random() * 10;  // 古いオルゴールのような狂い
      osc.connect(gain);
      gain.connect(master);
      gain.connect(echo);
      envelope(gain, at, i === 0 ? 0.07 : 0.025, 0.003, 1.4);
      osc.start(at);
      osc.stop(at + 1.5);
    });
  }

  /* 半音と増4度でぶつかる低いうなり。ゆっくり音程を揺らす */
  function drone(at, length) {
    [0, 1, 6].forEach(function (semitone, i) {
      var osc = ctx.createOscillator();
      var wobble = ctx.createOscillator();
      var depth = ctx.createGain();
      var gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.value = freq(semitone, -1);
      wobble.frequency.value = 0.2 + i * 0.13;
      depth.gain.value = 12;  // セント
      wobble.connect(depth);
      depth.connect(osc.detune);
      osc.connect(gain);
      gain.connect(master);
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.05, at + 1.5);
      gain.gain.setValueAtTime(0.05, at + length - 0.5);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
      osc.start(at);
      wobble.start(at);
      osc.stop(at + length + 0.05);
      wobble.stop(at + length + 0.05);
    });
  }

  /* ゾンビの声。のこぎり波を、声の響き（フォルマント）2つに通して「ヴォォ」「アァ」の母音にする。
     速い揺れで声をがらがらにし（うなり）、音程は少し上がってから力なく落ちる。far なら遠くでこもらせる */
  var VOWELS = [[450, 850], [600, 1000], [380, 750], [520, 1150]];  // オ、ア、ウ、エに近い響き
  function zombieVoice(at, options) {
    var pitch = options.pitch || 90;
    var length = options.length || 1.6;
    var vowel = VOWELS[Math.floor(Math.random() * VOWELS.length)];
    var osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(pitch, at);
    osc.frequency.linearRampToValueAtTime(pitch * (1.15 + Math.random() * 0.2), at + length * 0.3);
    osc.frequency.exponentialRampToValueAtTime(pitch * 0.55, at + length);
    // 音程のふらつき
    var jitter = ctx.createOscillator();
    var jitterDepth = ctx.createGain();
    jitter.frequency.value = 5 + Math.random() * 4;
    jitterDepth.gain.value = 25;
    jitter.connect(jitterDepth);
    jitterDepth.connect(osc.detune);
    // がらがら声。音量を速く揺らす
    var rasp = ctx.createGain();
    rasp.gain.value = 0.6;
    var raspOsc = ctx.createOscillator();
    var raspDepth = ctx.createGain();
    raspOsc.frequency.value = 28 + Math.random() * 22;
    raspDepth.gain.value = 0.4;
    raspOsc.connect(raspDepth);
    raspDepth.connect(rasp.gain);
    osc.connect(rasp);
    var out = ctx.createGain();
    vowel.forEach(function (hz, i) {
      var formant = ctx.createBiquadFilter();
      formant.type = "bandpass";
      formant.frequency.setValueAtTime(hz, at);
      formant.frequency.linearRampToValueAtTime(hz * 0.8, at + length);  // 口が閉じていく
      formant.Q.value = 6;
      var level = ctx.createGain();
      level.gain.value = i === 0 ? 1 : 0.6;
      rasp.connect(formant);
      formant.connect(level);
      level.connect(out);
    });
    var last = out;
    if (options.far) {
      var muffle = ctx.createBiquadFilter();
      muffle.type = "lowpass";
      muffle.frequency.value = 900;
      out.connect(muffle);
      last = muffle;
    }
    var gain = ctx.createGain();
    last.connect(gain);
    gain.connect(master);
    gain.connect(echo);
    var peak = options.gain || 0.4;
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(peak, at + Math.min(0.35, length * 0.3));
    gain.gain.setValueAtTime(peak, at + length * 0.6);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
    [osc, jitter, raspOsc].forEach(function (node) { node.start(at); node.stop(at + length + 0.05); });
  }

  /* 荒い息づかい。吸って（音が高くなる）、吐く（低くなる） */
  function breath(at, peak) {
    [[0, 0.35, 500, 1100], [0.4, 0.45, 1000, 450]].forEach(function (part) {
      var source = ctx.createBufferSource();
      source.buffer = noise;
      var filter = ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.Q.value = 1.5;
      filter.frequency.setValueAtTime(part[2], at + part[0]);
      filter.frequency.linearRampToValueAtTime(part[3], at + part[0] + part[1]);
      var gain = ctx.createGain();
      source.connect(filter);
      filter.connect(gain);
      gain.connect(master);
      gain.gain.setValueAtTime(0.0001, at + part[0]);
      gain.gain.exponentialRampToValueAtTime(peak, at + part[0] + part[1] * 0.4);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + part[0] + part[1]);
      source.start(at + part[0], Math.random() * 0.5);
      source.stop(at + part[0] + part[1] + 0.05);
    });
  }

  /* 足を引きずる音。こもった雑音を、ずるっと長く鳴らしてから、短く「トン」 */
  function shuffle(at, peak) {
    var source = ctx.createBufferSource();
    source.buffer = noise;
    var filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.setValueAtTime(500, at);
    filter.frequency.linearRampToValueAtTime(900, at + 0.3);
    var gain = ctx.createGain();
    source.connect(filter);
    filter.connect(gain);
    gain.connect(master);
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(peak, at + 0.18);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.38);
    source.start(at, Math.random() * 0.5);
    source.stop(at + 0.45);
    hit(at + 0.36, "lowpass", 300, peak * 1.6, 0.08);
  }

  /* 心臓の鼓動。低く短い音を、1拍目は強く、2拍目は弱く */
  function heartbeat(at, strong) {
    var osc = ctx.createOscillator();
    var gain = ctx.createGain();
    osc.frequency.setValueAtTime(strong ? 75 : 62, at);
    osc.frequency.exponentialRampToValueAtTime(38, at + 0.16);
    osc.connect(gain);
    gain.connect(master);
    envelope(gain, at, strong ? 0.85 : 0.5, 0.008, 0.2);
    osc.start(at);
    osc.stop(at + 0.3);
  }

  /* 弦をこするような高い音。2つの音を少しずらして重ね、ゆっくり半音ずり落とす */
  function screech(semitone, at, length) {
    [0, 23].forEach(function (detune) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      var tremolo = ctx.createOscillator();
      var depth = ctx.createGain();
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(freq(semitone, 1), at);
      osc.frequency.linearRampToValueAtTime(freq(semitone - 1, 1), at + length);
      osc.detune.value = detune;
      var filter = ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.frequency.value = freq(semitone, 1) * 2;
      filter.Q.value = 3;
      tremolo.frequency.value = 6.5;
      depth.gain.value = 0.012;
      tremolo.connect(depth);
      depth.connect(gain.gain);
      osc.connect(filter);
      filter.connect(gain);
      gain.connect(master);
      gain.connect(echo);
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.03, at + length * 0.4);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
      osc.start(at);
      tremolo.start(at);
      osc.stop(at + length + 0.05);
      tremolo.stop(at + length + 0.05);
    });
  }

  /* ささやき声のような雑音。口の形が変わるように、フィルターをふらふら動かす */
  function whisper(at, length) {
    var source = ctx.createBufferSource();
    source.buffer = noise;
    source.loop = true;
    var filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.Q.value = 9;
    var t = at;
    while (t < at + length) {
      filter.frequency.setValueAtTime(1200 + Math.random() * 2400, t);
      t += 0.08 + Math.random() * 0.12;
    }
    var gain = ctx.createGain();
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.09, at + length * 0.5);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + length);
    source.connect(filter);
    filter.connect(gain);
    gain.connect(echo);
    gain.connect(master);
    source.start(at);
    source.stop(at + length + 0.05);
  }

  /* 遠くで鉄を打つような音。倍音が整数倍にならない音を重ね、こだまに多く送る */
  function clang(at) {
    [1, 2.76, 5.4, 8.93].forEach(function (ratio, i) {
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = 180 * ratio;
      osc.connect(gain);
      gain.connect(echo);
      gain.connect(master);
      envelope(gain, at, 0.06 / (i + 1), 0.002, 1.6 - i * 0.3);
      osc.start(at);
      osc.stop(at + 1.7);
    });
  }

  function schedule(index, at) {
    var stepS = 60 / TEMPO / 4;
    var inBar = index % STEPS_PER_BAR;
    var loop = Math.floor(index / (STEPS_PER_BAR * LOOP_BARS));
    var bar = Math.floor(index / STEPS_PER_BAR) % LOOP_BARS;
    var chord = CHORDS[bar];
    var loopS = stepS * STEPS_PER_BAR * LOOP_BARS;
    if (inBar === 0) {
      pad(chord, at, stepS * STEPS_PER_BAR);
      if (bar === 0) { drone(at, loopS); }
      if (bar === LOOP_BARS - 1) { riser(at, stepS * STEPS_PER_BAR); }  // 最後の小節で、吸い込まれるように盛り上げる
    }

    // 心臓の鼓動。後半の2小節は少し強くして、迫ってくる感じにする。近づかれるほど速くなる
    if (threat > 0.5) {
      if (inBar % 2 === 0) { heartbeat(at, inBar % 4 === 0); }
    } else if (threat > 0.2) {
      if (inBar % 4 === 0 || inBar % 4 === 3) { heartbeat(at, inBar % 4 === 0); }
    } else {
      var beat = HEARTBEAT.indexOf(inBar);
      if (beat !== -1) { heartbeat(at, beat % 2 === 0 || bar >= 2); }
    }
    // 近づかれたら、すぐ後ろの荒い息づかいと、ときどき「ギャッ」という叫び声
    if (threat > 0.3 && inBar % 8 === 0) { breath(at, 0.05 + threat * 0.15); }
    if (threat > 0.5 && inBar === 10 && Math.random() < 0.4) {
      zombieVoice(at, { pitch: 230 + Math.random() * 80, length: 0.45, gain: 0.3 + threat * 0.2 });
    }
    // 時計の針のような、かすかな刻み
    if (inBar % 4 === 2) { hit(at, "highpass", 9000, 0.035, 0.02); }

    // こもった低い刻み。4小節かけて少しずつ開く
    var openness = (bar * STEPS_PER_BAR + inBar) / (LOOP_BARS * STEPS_PER_BAR);
    if (inBar % 2 === 0) {
      tone("sawtooth", freq(chord.root + OSTINATO[inBar], -1), at, stepS * 1.4, 0.16, 220 + 700 * openness);
    }
    if (inBar === 0) { tone("sine", freq(chord.root, -2), at, stepS * 14, 0.4); }  // 底で鳴る重い低音

    // 前半はオルゴール、後半は弦をこする音
    if (bar < 2) {
      var box = MUSIC_BOX[bar * STEPS_PER_BAR + inBar];
      if (box !== null) { musicBox(box, at); }
    } else if (inBar === 0) {
      screech(bar === 2 ? 13 : 18, at, stepS * STEPS_PER_BAR);
    }

    // ときどき混ざる物音。ループごとに場所を変えて、慣れさせない
    if (bar === 1 && inBar === 4 && loop % 2 === 0) { whisper(at, stepS * 12); }
    if (bar === (loop % 3 === 0 ? 2 : 3) && inBar === 6) { clang(at); }

    // ゾンビの気配。足を引きずる音は、遅いゾンビの歩みのように半小節に1回
    if (inBar === 6 || inBar === 14) { shuffle(at, bar >= 2 ? 0.12 : 0.08); }
    // 近くのうめき声は毎ループ、場所と高さを変えて1〜2回
    if (bar === 1 && inBar === 8) { zombieVoice(at, { pitch: 80 + Math.random() * 30, length: 1.8, gain: 0.45 }); }
    if (bar === 3 && inBar === 2 && loop % 2 === 1) { zombieVoice(at, { pitch: 120 + Math.random() * 20, length: 0.9, gain: 0.35 }); }  // 短いうなり
    // 遠くの群れ。2ループに1回、こもった声が少しずつずれて重なる
    if (bar === 0 && inBar === 4 && loop % 2 === 0) {
      [0, 0.4, 0.9, 1.3].forEach(function (delay, i) {
        zombieVoice(at + delay, { pitch: 70 + i * 12 + Math.random() * 10, length: 2.2, gain: 0.18, far: true });
      });
    }
  }

  function tick() {
    var stepS = 60 / TEMPO / 4;
    while (nextAt < ctx.currentTime + LOOKAHEAD_S) {
      schedule(step, nextAt);
      nextAt += stepS;
      step += 1;
    }
  }

  function start() {
    if (!playing || !enabled() || !setUp() || timer) { return; }
    if (ctx.state === "suspended") { ctx.resume(); }
    master.gain.cancelScheduledValues(ctx.currentTime);
    master.gain.setValueAtTime(VOLUME, ctx.currentTime);
    nextAt = ctx.currentTime + 0.05;
    step = 0;
    timer = setInterval(tick, TICK_MS);
  }

  function halt() {
    if (timer) { clearInterval(timer); timer = null; }
    if (master) {
      // 鳴っている音を短くフェードアウトさせる
      master.gain.cancelScheduledValues(ctx.currentTime);
      master.gain.setValueAtTime(master.gain.value, ctx.currentTime);
      master.gain.linearRampToValueAtTime(0, ctx.currentTime + 0.3);
    }
  }

  // 画面を離れている間は止め、戻ったら続きから鳴らす
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { halt(); } else { start(); }
  });

  /* 捕まったときの音。衝撃音、うなり声、かじる音を約3秒 */
  function captureSound() {
    var at = ctx.currentTime + 0.02;
    // 衝撃音
    var thud = ctx.createOscillator();
    var thudGain = ctx.createGain();
    thud.frequency.setValueAtTime(120, at);
    thud.frequency.exponentialRampToValueAtTime(30, at + 0.5);
    thud.connect(thudGain);
    thudGain.connect(fx);
    envelope(thudGain, at, 1, 0.005, 0.6);
    thud.start(at);
    thud.stop(at + 0.7);
    // 低いうなり声を、少しずつずらして3つ重ねる（群がっている感じ）
    [0, 0.35, 0.8].forEach(function (delay, i) {
      var osc = ctx.createOscillator();
      var filter = ctx.createBiquadFilter();
      var gain = ctx.createGain();
      var start = at + 0.1 + delay;
      osc.type = "sawtooth";
      osc.frequency.setValueAtTime(80 + i * 18, start);
      osc.frequency.linearRampToValueAtTime(105 + i * 15, start + 0.5);
      osc.frequency.exponentialRampToValueAtTime(48 + i * 8, start + 2.2);
      filter.type = "bandpass";
      filter.Q.value = 4;
      filter.frequency.setValueAtTime(320, start);
      filter.frequency.linearRampToValueAtTime(650, start + 0.6);
      filter.frequency.exponentialRampToValueAtTime(220, start + 2.2);
      osc.connect(filter);
      filter.connect(gain);
      gain.connect(fx);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.5, start + 0.3);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 2.3);
      osc.start(start);
      osc.stop(start + 2.4);
    });
    // かじる音。短くこもった雑音を不規則に鳴らす
    var bite = at + 0.6;
    while (bite < at + 3) {
      var source = ctx.createBufferSource();
      source.buffer = noise;
      var crunch = ctx.createBiquadFilter();
      crunch.type = "bandpass";
      crunch.frequency.value = 700 + Math.random() * 900;
      crunch.Q.value = 2;
      var gain = ctx.createGain();
      source.connect(crunch);
      crunch.connect(gain);
      gain.connect(fx);
      envelope(gain, bite, 0.7 + Math.random() * 0.3, 0.004, 0.07 + Math.random() * 0.06);
      source.start(bite, Math.random() * 0.5);
      source.stop(bite + 0.2);
      bite += 0.12 + Math.random() * 0.22;
    }
  }

  /* シェルターに着いた合図。画面を見ていなくても分かるよう、明るい音を3つ鳴らす */
  function arrivalSound() {
    var at = ctx.currentTime + 0.02;
    [660, 880, 1320].forEach(function (frequency, i) {
      var start = at + i * 0.18;
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.type = "triangle";
      osc.frequency.value = frequency;
      osc.connect(gain);
      gain.connect(fx);
      envelope(gain, start, 0.8, 0.01, 0.3);
      osc.start(start);
      osc.stop(start + 0.4);
    });
  }

  window.ZombieBGM = {
    /* タップの中で呼ぶ。これがないと、スマホでは音が出ない */
    unlock: function () {
      if (!setUp()) { return; }
      if (enabled()) { keepSessionPlaying(); }
      if (ctx.state === "suspended") { ctx.resume(); }
      if (playing) { start(); }
    },
    play: function () {
      playing = true;
      start();
    },
    stop: function () {
      playing = false;
      halt();
      if (silence) { silence.pause(); }
    },
    /* 捕まったとき。BGMを止めて、捕まった音を鳴らす（BGMをオフにしていれば鳴らさない） */
    captured: function () {
      playing = false;
      halt();
      if (enabled() && ctx && ctx.state === "running") { captureSound(); }
    },
    /* シェルターに着いたとき。BGMのオン・オフに関わらず鳴らす（画面を見ていない人への合図） */
    arrived: function () {
      if (ctx && ctx.state === "running") { arrivalSound(); }
    },
    /* 近づかれた怖さ（0〜1）を渡す */
    setThreat: function (value) { threat = Math.max(0, Math.min(1, value || 0)); },
    enabled: enabled,
    setEnabled: function (on) {
      try { localStorage.setItem(STORAGE_KEY, on ? "on" : "off"); } catch (e) { /* 覚えられなくても切り替えはする */ }
      if (on) {
        keepSessionPlaying();
        start();
      } else {
        halt();
        if (silence) { silence.pause(); }
      }
    },
    /* 音を出せる状態か（一度もタップしていなければ false） */
    ready: function () { return Boolean(ctx) && ctx.state === "running"; },
  };
})();
