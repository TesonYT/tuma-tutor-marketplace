// Ethel human tutor track (docs: Ethel Tutor Marketplace - Design Spec). Mount with:
//   const tutorTrack = require('./tutor-track'); tutorTrack.mount(app);
const db = require('./db');
const publicRoutes = require('./public-routes');
const adminRoutes = require('./admin-routes');

function mount(app) {
  // Wrap async handlers' rejections so a DB hiccup returns a 500 page instead of hanging the request.
  for (const router of [publicRoutes, adminRoutes]) {
    for (const layer of router.stack) {
      if (!layer.route) continue;
      for (const l of layer.route.stack) {
        const fn = l.handle;
        if (fn.constructor.name === 'AsyncFunction') {
          l.handle = (req, res, next) => fn(req, res, next).catch(next);
        }
      }
    }
  }
  app.use(publicRoutes);
  app.use(adminRoutes);
  return db.init().then((on) => {
    console.log(on ? 'Tutor track: database ready' : 'Tutor track: disabled (set DATABASE_URL to enable)');
  }).catch((err) => {
    console.error('Tutor track: database init failed:', err.message);
  });
}

module.exports = { mount };
