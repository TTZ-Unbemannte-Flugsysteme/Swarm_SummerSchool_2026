# Assembly & Wiring Flowchart

<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
    .glass-card {
        background: rgba(15, 23, 42, 0.75);
        backdrop-filter: blur(12px);
        border: 1px solid rgba(255, 255, 255, 0.08);
        box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }
    .flow-line {
        position: absolute;
        left: 2rem;
        top: 2rem;
        bottom: 4rem;
        width: 3px;
        background: linear-gradient(to bottom, #38bdf8, #a855f7, #10b981, #f59e0b);
        z-index: 0;
        opacity: 0.6;
    }
</style>

<div class="relative max-w-4xl mx-auto mt-10 pb-12">
    <!-- Connecting Background Line for Desktop -->
    <div class="flow-line hidden md:block"></div>
    
    <!-- Step 1: Base Frame & Power -->
    <div class="relative z-10 flex flex-col md:flex-row items-start mb-12 group">
        <div class="hidden md:flex flex-col items-center mr-6">
            <div class="w-16 h-16 rounded-full bg-slate-900 border-2 border-sky-500 flex items-center justify-center shadow-[0_0_20px_rgba(56,189,248,0.4)] group-hover:scale-110 group-hover:shadow-[0_0_30px_rgba(56,189,248,0.7)] transition duration-500">
                <i class="fa-solid fa-bolt text-sky-400 text-2xl"></i>
            </div>
        </div>
        <div class="glass-card rounded-2xl p-6 sm:p-8 border border-slate-800 flex-1 hover:border-sky-500/50 transition duration-300 w-full relative overflow-hidden">
            <div class="absolute right-0 top-0 w-32 h-32 bg-sky-500/10 rounded-bl-[100px] -z-10 transition duration-500 group-hover:bg-sky-500/20"></div>
            <h3 class="text-xl font-bold text-sky-400 mb-4">1. Power Distribution Board (PDB)</h3>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-700/60 shadow-inner">
                    <div class="text-sm font-bold text-slate-200 mb-1">Weld XT90 Lead</div>
                    <div class="text-xs text-sky-200/60">Main Battery Input</div>
                </div>
                <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-700/60 shadow-inner">
                    <div class="text-sm font-bold text-slate-200 mb-1">Weld 4x 30A ESCs</div>
                    <div class="text-xs text-sky-200/60">Motor Power Pads</div>
                </div>
                <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-700/60 shadow-inner">
                    <div class="text-sm font-bold text-slate-200 mb-1">Weld 5V 3A UBEC</div>
                    <div class="text-xs text-sky-200/60">Step-down for Pi 4</div>
                </div>
                <div class="bg-slate-900/80 p-4 rounded-xl border border-slate-700/60 shadow-inner">
                    <div class="text-sm font-bold text-slate-200 mb-1">Weld VBAT Wire</div>
                    <div class="text-xs text-sky-200/60">Power to FC</div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Step 2: Frame Construction -->
    <div class="relative z-10 flex flex-col md:flex-row items-start mb-12 group">
        <div class="hidden md:flex flex-col items-center mr-6">
            <div class="w-16 h-16 rounded-full bg-slate-900 border-2 border-purple-500 flex items-center justify-center shadow-[0_0_20px_rgba(168,85,247,0.4)] group-hover:scale-110 group-hover:shadow-[0_0_30px_rgba(168,85,247,0.7)] transition duration-500">
                <i class="fa-solid fa-layer-group text-purple-400 text-2xl"></i>
            </div>
        </div>
        <div class="glass-card rounded-2xl p-6 sm:p-8 border border-slate-800 flex-1 hover:border-purple-500/50 transition duration-300 w-full relative overflow-hidden">
            <div class="absolute right-0 top-0 w-32 h-32 bg-purple-500/10 rounded-bl-[100px] -z-10 transition duration-500 group-hover:bg-purple-500/20"></div>
            <h3 class="text-xl font-bold text-purple-400 mb-4">2. Frame & Motor Mounting</h3>
            <ul class="text-sm text-slate-300 space-y-4">
                <li class="flex items-center bg-slate-900/50 p-3 rounded-lg"><i class="fa-solid fa-arrow-right text-purple-500/70 mr-3 text-sm"></i> Sandwich F450 Arms between PDB and Top Plate</li>
                <li class="flex items-center bg-slate-900/50 p-3 rounded-lg"><i class="fa-solid fa-arrow-right text-purple-500/70 mr-3 text-sm"></i> Attach Landing Gear using 2.0mm Hex Screws</li>
                <li class="flex items-center bg-slate-900/50 p-3 rounded-lg"><i class="fa-solid fa-arrow-right text-purple-500/70 mr-3 text-sm"></i> Mount 2212 Motors to Arms using 2.0mm Hex Screws</li>
                <li class="flex items-center bg-slate-900/50 p-3 rounded-lg"><i class="fa-solid fa-arrow-right text-purple-500/70 mr-3 text-sm"></i> Connect ESC bullet connectors to Motor wires</li>
            </ul>
        </div>
    </div>
    
    <!-- Step 3: Flight Controller Wiring -->
    <div class="relative z-10 flex flex-col md:flex-row items-start mb-12 group">
        <div class="hidden md:flex flex-col items-center mr-6">
            <div class="w-16 h-16 rounded-full bg-slate-900 border-2 border-emerald-500 flex items-center justify-center shadow-[0_0_20px_rgba(16,185,129,0.4)] group-hover:scale-110 group-hover:shadow-[0_0_30px_rgba(16,185,129,0.7)] transition duration-500">
                <i class="fa-solid fa-microchip text-emerald-400 text-2xl"></i>
            </div>
        </div>
        <div class="glass-card rounded-2xl p-6 sm:p-8 border border-slate-800 flex-1 hover:border-emerald-500/50 transition duration-300 w-full relative overflow-hidden">
            <div class="absolute right-0 top-0 w-32 h-32 bg-emerald-500/10 rounded-bl-[100px] -z-10 transition duration-500 group-hover:bg-emerald-500/20"></div>
            <h3 class="text-xl font-bold text-emerald-400 mb-2">3. Flight Controller Pinouts</h3>
            <p class="text-xs text-slate-400 mb-6">Mount Matek H743-SLIM on vibration dampeners, then connect components to these specific pads:</p>
            
            <div class="space-y-3">
                <div class="flex items-center justify-between bg-slate-900/80 p-3 sm:p-4 rounded-xl border border-slate-700/60 shadow-inner hover:border-emerald-500/30 transition">
                    <div class="text-sm font-bold text-slate-300 flex items-center"><i class="fa-solid fa-fan w-6 text-center text-slate-500 mr-2"></i> ESC Signals</div>
                    <div class="text-xs font-mono bg-emerald-500/20 text-emerald-300 px-3 py-1.5 rounded-md border border-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.2)]">S1, S2, S3, S4 Pads</div>
                </div>
                
                <div class="flex items-center justify-between bg-slate-900/80 p-3 sm:p-4 rounded-xl border border-slate-700/60 shadow-inner hover:border-purple-500/30 transition">
                    <div class="text-sm font-bold text-slate-300 flex items-center"><i class="fa-solid fa-satellite-dish w-6 text-center text-slate-500 mr-2"></i> FrSky Receiver</div>
                    <div class="text-xs font-mono bg-purple-500/20 text-purple-300 px-3 py-1.5 rounded-md border border-purple-500/20 shadow-[0_0_10px_rgba(168,85,247,0.2)]">RX6 Pad (SBUS) + 5V/GND</div>
                </div>
                
                <div class="flex items-center justify-between bg-slate-900/80 p-3 sm:p-4 rounded-xl border border-slate-700/60 shadow-inner hover:border-sky-500/30 transition">
                    <div class="text-sm font-bold text-slate-300 flex items-center"><i class="fa-solid fa-location-dot w-6 text-center text-slate-500 mr-2"></i> GPS & Compass</div>
                    <div class="text-xs font-mono bg-sky-500/20 text-sky-300 px-3 py-1.5 rounded-md border border-sky-500/20 text-right shadow-[0_0_10px_rgba(56,189,248,0.2)]">
                        <div>UART2 (TX2/RX2)</div>
                        <div class="mt-1 border-t border-sky-500/30 pt-1">I2C1 (SDA1/SCL1)</div>
                    </div>
                </div>
                
                <div class="flex items-center justify-between bg-slate-900/80 p-3 sm:p-4 rounded-xl border border-slate-700/60 shadow-inner hover:border-rose-500/30 transition">
                    <div class="text-sm font-bold text-slate-300 flex items-center"><i class="fa-brands fa-raspberry-pi w-6 text-center text-slate-500 mr-2"></i> Raspberry Pi 4</div>
                    <div class="text-xs font-mono bg-rose-500/20 text-rose-300 px-3 py-1.5 rounded-md border border-rose-500/20 shadow-[0_0_10px_rgba(244,63,94,0.2)]">UART1 (TX1/RX1)</div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Step 4: Finalization -->
    <div class="relative z-10 flex flex-col md:flex-row items-start group">
        <div class="hidden md:flex flex-col items-center mr-6 relative">
            <div class="w-16 h-16 rounded-full bg-slate-950 border-2 border-amber-500 flex items-center justify-center shadow-[0_0_20px_rgba(245,158,11,0.4)] group-hover:scale-110 group-hover:shadow-[0_0_30px_rgba(245,158,11,0.7)] transition duration-500">
                <i class="fa-solid fa-check-double text-amber-400 text-2xl"></i>
            </div>
        </div>
        <div class="glass-card rounded-2xl p-6 border border-amber-500/30 flex-1 hover:border-amber-500/60 transition duration-300 w-full bg-gradient-to-r from-slate-900 to-amber-900/20 shadow-[0_10px_30px_rgba(245,158,11,0.1)]">
            <h3 class="text-lg font-bold text-amber-400 mb-2">4. Final Assembly & QA</h3>
            <p class="text-sm text-slate-300">Zip-tie all loose cables flush with the frame. Perform a final continuity check with a multimeter before connecting the battery.</p>
        </div>
    </div>
    
</div>
