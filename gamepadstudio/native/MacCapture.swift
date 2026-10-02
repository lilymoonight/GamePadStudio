// ScreenCaptureKit frames stay floating point until explicit SDR tone mapping.
// Metadata and synthetic commands never access screen pixels or request TCC.
import AppKit
import CoreGraphics
import CoreImage
import CoreMedia
import CoreVideo
import AudioToolbox
import ScreenCaptureKit

let captureBackground = CGColor(gray: 0, alpha: 1)

struct CaptureFailure: Error, CustomStringConvertible {
    let description: String
    init(_ description: String) { self.description = description }
}

func hdrAPIAvailable() -> Bool {
    #if arch(arm64)
    if #available(macOS 15.0, *) { return true }
    #endif
    return false
}

func displays() -> [[String: Any]] {
    _ = NSApplication.shared
    return NSScreen.screens.compactMap { screen in
        guard let id = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? UInt32,
              CGDisplayIsActive(id) != 0, let mode = CGDisplayCopyDisplayMode(id) else { return nil }
        let r = CGDisplayBounds(id)
        let current = screen.maximumExtendedDynamicRangeColorComponentValue
        let potential = screen.maximumPotentialExtendedDynamicRangeColorComponentValue
        let refresh = mode.refreshRate
        let rotated = Int(CGDisplayRotation(id).rounded()) % 180 != 0
        let pixelWidth = rotated ? mode.pixelHeight : mode.pixelWidth
        let pixelHeight = rotated ? mode.pixelWidth : mode.pixelHeight
        return ["display_id": id, "device_name": "CGDisplay:\(id)", "friendly_name": screen.localizedName,
                "left": r.minX, "top": r.minY, "width": r.width, "height": r.height,
                "bounds_points": ["left": r.minX, "top": r.minY, "width": r.width, "height": r.height],
                "pixel_width": pixelWidth, "pixel_height": pixelHeight,
                "width_px": pixelWidth, "height_px": pixelHeight, "rotation": CGDisplayRotation(id),
                "scale": Double(pixelWidth) / max(1, r.width), "backing_scale": screen.backingScaleFactor,
                "primary": id == CGMainDisplayID(), "refresh_hz": refresh > 0 ? refresh : NSNull(),
                "refresh_source": "CGDisplayModeGetRefreshRate", "maximum_fps": screen.maximumFramesPerSecond,
                "minimum_refresh_interval": screen.minimumRefreshInterval,
                "maximum_refresh_interval": screen.maximumRefreshInterval,
                "hdr_enabled": current > 1, "hdr_supported": potential > 1,
                "edr_headroom": current, "edr_potential_headroom": potential,
                "edr_reference_headroom": screen.maximumReferenceExtendedDynamicRangeColorComponentValue,
                "hdr_user_enabled": NSNull(), "sdr_white_nits": NSNull(),
                "color_source": "NSScreen EDR headroom", "color_mode": current > 1 ? "edr" : "sdr",
                "hdr_capture_available": hdrAPIAvailable(), "coordinate_space": "cg-points"]
    }.sorted { ($0["primary"] as? Bool == true) && ($1["primary"] as? Bool != true) }
}

func writeJSON(_ value: Any) throws {
    let json = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
    FileHandle.standardOutput.write(json)
    FileHandle.standardOutput.write(Data([10]))
}

func windowMetadata(_ identity: [String: Any]) throws -> [String: Double]? {
    guard let id = identity["window_id"] as? UInt32 else { return nil }
    guard let rows = CGWindowListCopyWindowInfo(.optionIncludingWindow, id) as? [[String: Any]],
          let row = rows.first(where: { ($0[kCGWindowNumber as String] as? NSNumber)?.uint32Value == id }),
          (row[kCGWindowOwnerPID as String] as? NSNumber)?.int32Value == (identity["pid"] as? NSNumber)?.int32Value,
          let bounds = row[kCGWindowBounds as String] as? [String: Any],
          let width = (bounds["Width"] as? NSNumber)?.doubleValue,
          let height = (bounds["Height"] as? NSNumber)?.doubleValue,
          width.isFinite, height.isFinite, width > 0, height > 0 else {
        throw CaptureFailure("Selected window disappeared or its owner changed; capture stopped")
    }
    return ["width": width, "height": height]
}

final class ColorPipeline {
    let linear = CGColorSpace(name: CGColorSpace.extendedLinearSRGB)!
    let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
    let context: CIContext
    let kernel: CIColorKernel
    init() throws {
        context = CIContext(options: [.workingColorSpace: linear, .workingFormat: CIFormat.RGBAh.rawValue,
                                     .cacheIntermediates: false])
        guard let shader = CIColorKernel(source: """
        kernel vec4 toSDR(__sample source, float tone, float video) {
            vec3 c = max(source.rgb, vec3(0.0));
            float signal = max(max(c.r, c.g), c.b);
            if (tone > 0.5 && signal > 0.75) {
                float over = signal - 0.75;
                float mapped = 0.75 + 0.25 * over / (over + 0.25);
                c *= mapped / signal;
            }
            c = clamp(c, vec3(0.0), vec3(1.0));
            // Captured desktops are display referred: use inverse BT.1886
            // for BT.709 display output, like zimg's non-scene-referred path.
            if (video > 0.5) c = pow(c, vec3(1.0 / 2.4));
            return vec4(c, 1.0);
        }
        """) else { throw CaptureFailure("Core Image HDR color kernel unavailable") }
        kernel = shader
    }
    func render(_ image: CIImage, width: Int, height: Int, hdr: Bool, transfer: String) throws -> Data {
        guard width > 0, height > 0, width <= 32768, height <= 16384,
              width * height <= 67108864 else { throw CaptureFailure("Invalid capture pixel dimensions") }
        let rect = CGRect(x: 0, y: 0, width: width, height: height)
        let image = image.transformed(by: CGAffineTransform(translationX: -image.extent.minX, y: -image.extent.minY))
        guard let mapped = kernel.apply(extent: rect, arguments: [image, hdr ? 1.0 : 0.0, transfer == "bt709" ? 1.0 : 0.0]) else {
            throw CaptureFailure("HDR tone mapping failed")
        }
        var rgba = Data(count: width * height * 4)
        rgba.withUnsafeMutableBytes { buffer in
            context.render(mapped, toBitmap: buffer.baseAddress!, rowBytes: width * 4, bounds: rect,
                           format: .RGBA8, colorSpace: transfer == "bt709" ? linear : srgb)
        }
        var rgb = Data(count: width * height * 3)
        try rgb.withUnsafeMutableBytes { target in
            try rgba.withUnsafeBytes { source in
                let src = source.bindMemory(to: UInt8.self), dst = target.bindMemory(to: UInt8.self)
                for i in 0..<(width * height) {
                    // CIContext can silently fail to render when GPU/WindowServer
                    // access is unavailable. The kernel always writes opaque alpha;
                    // an untouched zero buffer must never masquerade as valid black.
                    guard src[i*4+3] == 255 else { throw CaptureFailure("Core Image render unavailable; frame discarded") }
                    dst[i*3] = src[i*4]; dst[i*3+1] = src[i*4+1]; dst[i*3+2] = src[i*4+2]
                }
            }
        }
        return rgb
    }
    func sample(_ sample: CMSampleBuffer, hdr: Bool, transfer: String, identity: [String: Any]) throws {
        let windowSize = try windowMetadata(identity)
        guard let pixel = CMSampleBufferGetImageBuffer(sample) else { throw CaptureFailure("Missing image buffer") }
        let format = CVPixelBufferGetPixelFormatType(pixel)
        let color = CVBufferCopyAttachment(pixel, kCVImageBufferCGColorSpaceKey, nil)
        let primaries = CVBufferCopyAttachment(pixel, kCVImageBufferColorPrimariesKey, nil)
        let function = CVBufferCopyAttachment(pixel, kCVImageBufferTransferFunctionKey, nil)
        let linear709 = (primaries.map { CFEqual($0, kCVImageBufferColorPrimaries_ITU_R_709_2) } == true &&
                         function.map { CFEqual($0, kCVImageBufferTransferFunction_Linear) } == true)
        let colorMatches = color.map { CFEqual($0, linear) } == true
        if hdr && (format != kCVPixelFormatType_64RGBAHalf || !(colorMatches || linear709)) {
            throw CaptureFailure("HDR requires a verified RGBA-half / linear BT.709 buffer; capture was refused")
        }
        if !hdr && format != kCVPixelFormatType_32BGRA { throw CaptureFailure("SDR capture returned an unexpected pixel format") }
        let w = CVPixelBufferGetWidth(pixel), h = CVPixelBufferGetHeight(pixel)
        let image = CIImage(cvPixelBuffer: pixel, options: [.colorSpace: hdr ? linear : srgb, .toneMapHDRtoSDR: false])
        let data = try render(image, width: w, height: h, hdr: hdr, transfer: transfer)
        var header = identity
        if let windowSize {
            guard windowSize == (try windowMetadata(identity)) else { throw CaptureFailure("Window size changed during capture") }
            header["window_size_points"] = windowSize
        }
        let timestamp = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
        guard timestamp.isFinite else { throw CaptureFailure("Capture has no valid source timestamp") }
        header.merge(["version": 1, "type": "video", "width": w, "height": h, "bytes": data.count, "timestamp": timestamp,
                      "pixel_format": "rgb24", "source_pixel_format": hdr ? "rgba16f" : "bgra8",
                      "source_color_space": hdr ? "extended-linear-srgb" : "srgb", "transfer": transfer,
                      "hdr_capture": hdr, "hdr_verified": hdr, "tone_mapped": hdr,
                      "color_primaries": "bt709", "color_managed": true], uniquingKeysWith: { _, new in new })
        try writeJSON(header)
        FileHandle.standardOutput.write(data)
    }
}

func convertPCM(_ format: AudioStreamBasicDescription, samples: Int, buffers: UnsafeMutableAudioBufferListPointer) throws -> Data {
    let planar = format.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
    let floating = format.mFormatFlags & kAudioFormatFlagIsFloat != 0
    let signed = format.mFormatFlags & kAudioFormatFlagIsSignedInteger != 0
    let bytesPerSample = Int(format.mBitsPerChannel / 8)
    guard format.mFormatID == kAudioFormatLinearPCM, format.mSampleRate == 48000, format.mChannelsPerFrame == 2,
          format.mFormatFlags & kAudioFormatFlagIsBigEndian == 0,
          (floating && bytesPerSample == 4) || (signed && bytesPerSample == 2),
          samples > 0, samples <= 48000, buffers.count == (planar ? 2 : 1),
          Int(format.mBytesPerFrame) == bytesPerSample * (planar ? 1 : 2) else {
        throw CaptureFailure("Unsupported system audio PCM format; audio capture refused")
    }
    for buffer in buffers {
        guard buffer.mData != nil, buffer.mNumberChannels == (planar ? 1 : 2),
              Int(buffer.mDataByteSize) >= samples * bytesPerSample * (planar ? 1 : 2) else {
            throw CaptureFailure("Incomplete system audio buffer")
        }
    }
    var output = Data(count: samples * 4)
    try output.withUnsafeMutableBytes { raw in
        let out = raw.bindMemory(to: Int16.self)
        for i in 0..<samples {
            for channel in 0..<2 {
                let buffer = buffers[planar ? channel : 0]
                let index = planar ? i : i*2+channel
                let value: Int16
                if floating {
                    let input = buffer.mData!.assumingMemoryBound(to: Float32.self)[index]
                    guard input.isFinite else { throw CaptureFailure("Invalid system audio sample") }
                    value = Int16(max(-32768, min(32767, Int((Double(input) * 32768).rounded()))))
                } else { value = buffer.mData!.assumingMemoryBound(to: Int16.self)[index] }
                out[i*2+channel] = value.littleEndian
            }
        }
    }
    return output
}

func audioSample(_ sample: CMSampleBuffer, scope: String = "system") throws {
    guard let description = CMSampleBufferGetFormatDescription(sample),
          let format = CMAudioFormatDescriptionGetStreamBasicDescription(description) else {
        throw CaptureFailure("Missing system audio format description")
    }
    var size = 0
    let query = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(sample, bufferListSizeNeededOut: &size,
                       bufferListOut: nil, bufferListSize: 0, blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
                       flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment), blockBufferOut: nil)
    guard query == noErr, size > 0, size <= 4096 else { throw CaptureFailure("Cannot read system audio buffer list") }
    let allocation = UnsafeMutableRawPointer.allocate(byteCount: size, alignment: 16)
    defer { allocation.deallocate() }
    let list = allocation.bindMemory(to: AudioBufferList.self, capacity: 1)
    var block: CMBlockBuffer?
    let result = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(sample, bufferListSizeNeededOut: nil,
                       bufferListOut: list, bufferListSize: size, blockBufferAllocator: nil, blockBufferMemoryAllocator: nil,
                       flags: UInt32(kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment), blockBufferOut: &block)
    guard result == noErr else { throw CaptureFailure("System audio data is not ready") }
    defer { withExtendedLifetime(block) {} }
    let data = try convertPCM(format.pointee, samples: CMSampleBufferGetNumSamples(sample),
                              buffers: UnsafeMutableAudioBufferListPointer(list))
    let timestamp = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sample))
    guard timestamp.isFinite else { throw CaptureFailure("System audio has no valid source timestamp") }
    try writeJSON(["version":1, "type":"audio", "bytes":data.count, "timestamp":timestamp,
                   "audio_format":"s16le", "audio_source":"system", "microphone":false,
                   "audio_scope":scope, "sample_rate":48000, "channels":2])
    FileHandle.standardOutput.write(data)
}

@available(macOS 14.0, *)
final class CaptureOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    let pipeline: ColorPipeline
    let hdr: Bool
    let transfer: String
    let identity: [String: Any]
    let lock = NSLock()
    init(pipeline: ColorPipeline, hdr: Bool, transfer: String, identity: [String: Any]) {
        self.pipeline = pipeline; self.hdr = hdr; self.transfer = transfer; self.identity = identity
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard sampleBuffer.isValid, type == .screen || type == .audio else { return }
        if type == .screen, let array = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
           let raw = array.first?[.status] as? Int, raw != SCFrameStatus.complete.rawValue { return }
        lock.lock(); defer { lock.unlock() }
        do {
            if type == .audio { try audioSample(sampleBuffer, scope: identity["window_id"] != nil ? "application" : "system") }
            else { try pipeline.sample(sampleBuffer, hdr: hdr, transfer: transfer, identity: identity) }
        }
        catch { fail(error) }
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) { fail(error) }
}

func fail(_ error: Error) -> Never {
    FileHandle.standardError.write(Data(("MAC_CAPTURE_ERROR: \(error)\n").utf8))
    exit(1)
}

@available(macOS 14.0, *)
func capture(_ args: [String]) async throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw CaptureFailure("Screen recording permission required; grant it in System Settings, then restart the app")
    }
    guard args.count >= 5, let id = UInt32(args[2]), let fps = Int32(args[4]), fps > 0 && fps <= 240,
          ["srgb", "bt709"].contains(args[3]) else { throw CaptureFailure("Invalid capture arguments") }
    let window = args[1] == "window"
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    let filter: SCContentFilter
    var identity: [String: Any]
    var hdr = false
    if window {
        guard let target = content.windows.first(where: { $0.windowID == id }) else { throw CaptureFailure("Selected window disappeared") }
        filter = SCContentFilter(desktopIndependentWindow: target)
        identity = ["window_id": id, "pid": target.owningApplication?.processID ?? 0]
        let touched = displays().filter { row in
            let rect = CGRect(x: (row["left"] as! NSNumber).doubleValue, y: (row["top"] as! NSNumber).doubleValue,
                              width: (row["width"] as! NSNumber).doubleValue, height: (row["height"] as! NSNumber).doubleValue)
            return rect.intersects(target.frame)
        }
        hdr = touched.contains { $0["hdr_enabled"] as? Bool == true }
    } else {
        guard let target = content.displays.first(where: { $0.displayID == id }) else { throw CaptureFailure("Selected display disappeared") }
        filter = SCContentFilter(display: target, excludingWindows: [])
        identity = ["display_id": id]
        hdr = displays().first(where: { $0["display_id"] as? UInt32 == id })?["hdr_enabled"] as? Bool == true
    }
    if hdr && !hdrAPIAvailable() { throw CaptureFailure("HDR capture requires macOS 15 or later on Apple Silicon") }
    let config = SCStreamConfiguration()
    config.width = max(2, Int((filter.contentRect.width * CGFloat(filter.pointPixelScale)).rounded()))
    config.height = max(2, Int((filter.contentRect.height * CGFloat(filter.pointPixelScale)).rounded()))
    config.minimumFrameInterval = CMTime(value: 1, timescale: fps)
    config.pixelFormat = hdr ? kCVPixelFormatType_64RGBAHalf : kCVPixelFormatType_32BGRA
    config.colorSpaceName = hdr ? CGColorSpace.extendedLinearSRGB : CGColorSpace.sRGB
    config.colorMatrix = kCVImageBufferYCbCrMatrix_ITU_R_709_2
    config.showsCursor = false
    let audio = args[0] == "stream" && args.count > 5 && args[5] == "system-audio"
    config.capturesAudio = audio
    config.sampleRate = 48000
    config.channelCount = 2
    config.excludesCurrentProcessAudio = true
    config.queueDepth = 2
    config.ignoreShadowsSingleWindow = true
    config.backgroundColor = captureBackground
    if #available(macOS 15.0, *) {
        config.captureDynamicRange = hdr ? .hdrLocalDisplay : .SDR
        config.captureMicrophone = false
    }
    let pipeline = try ColorPipeline()
    if args[0] == "shot" {
        let sample = try await SCScreenshotManager.captureSampleBuffer(contentFilter: filter, configuration: config)
        try pipeline.sample(sample, hdr: hdr, transfer: args[3], identity: identity)
        exit(0)
    }
    let output = CaptureOutput(pipeline: pipeline, hdr: hdr, transfer: args[3], identity: identity)
    let stream = SCStream(filter: filter, configuration: config, delegate: output)
    try stream.addStreamOutput(output, type: .screen, sampleHandlerQueue: DispatchQueue(label: "ScreenFrames"))
    if audio { try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: DispatchQueue(label: "SystemAudio")) }
    try await stream.startCapture()
    // Keep stream/output alive; stdout is a bounded pipe, no frames are saved.
    let parent = getppid()
    while getppid() == parent { try await Task.sleep(nanoseconds: 1_000_000_000); _ = stream; _ = output }
    try await stream.stopCapture()
    exit(0)
}

func synthetic(_ transfer: String) throws {
    let values: [Float16] = [0,0,0,1, 0.18,0.18,0.18,1, 1,1,1,1, 2,2,2,1, 8,8,8,1, 4,2,1,1]
    let bytes = values.withUnsafeBytes { Data($0) }
    let space = CGColorSpace(name: CGColorSpace.extendedLinearSRGB)!
    let image = CIImage(bitmapData: bytes, bytesPerRow: 48, size: CGSize(width: 6, height: 1), format: .RGBAh, colorSpace: space)
    let data = try ColorPipeline().render(image, width: 6, height: 1, hdr: true, transfer: transfer)
    try writeJSON(["version":1, "synthetic": true, "width":6, "height":1, "bytes":18,
                   "pixel_format":"rgb24", "source_pixel_format":"rgba16f", "source_color_space":"extended-linear-srgb",
                   "hdr_capture":true, "hdr_verified":true, "tone_mapped":true, "color_managed":true,
                   "color_primaries":"bt709", "transfer":transfer, "timestamp":0.0])
    FileHandle.standardOutput.write(data)
}

func syntheticAudio(_ planar: Bool) throws {
    let count = 4800
    var floats = [Float32](repeating: 0, count: count*2)
    for i in 0..<count {
        let value = Float32(sin(Double(i)*2*Double.pi*440/48000)*0.25)
        floats[planar ? i : i*2] = value
        floats[planar ? count+i : i*2+1] = -value
    }
    var format = AudioStreamBasicDescription(mSampleRate: 48000, mFormatID: kAudioFormatLinearPCM,
                  mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked | (planar ? kAudioFormatFlagIsNonInterleaved : 0),
                  mBytesPerPacket: UInt32(planar ? 4 : 8), mFramesPerPacket: 1,
                  mBytesPerFrame: UInt32(planar ? 4 : 8), mChannelsPerFrame: 2, mBitsPerChannel: 32, mReserved: 0)
    var description: CMAudioFormatDescription?
    guard CMAudioFormatDescriptionCreate(allocator: kCFAllocatorDefault, asbd: &format, layoutSize: 0, layout: nil,
                  magicCookieSize: 0, magicCookie: nil, extensions: nil, formatDescriptionOut: &description) == noErr else {
        throw CaptureFailure("Synthetic audio description failed")
    }
    let data = floats.withUnsafeBytes { Data($0) }
    var block: CMBlockBuffer?
    guard CMBlockBufferCreateWithMemoryBlock(allocator: kCFAllocatorDefault, memoryBlock: nil, blockLength: data.count,
                  blockAllocator: kCFAllocatorDefault, customBlockSource: nil, offsetToData: 0,
                  dataLength: data.count, flags: 0, blockBufferOut: &block) == noErr, let block else {
        throw CaptureFailure("Synthetic audio allocation failed")
    }
    let copied = data.withUnsafeBytes { CMBlockBufferReplaceDataBytes(with: $0.baseAddress!, blockBuffer: block,
                                                                   offsetIntoDestination: 0, dataLength: data.count) }
    guard copied == noErr else { throw CaptureFailure("Synthetic audio copy failed") }
    var timing = CMSampleTimingInfo(duration: CMTime(value: 1, timescale: 48000),
                  presentationTimeStamp: CMTime(value: 123000, timescale: 1000), decodeTimeStamp: .invalid)
    var sample: CMSampleBuffer?
    guard CMSampleBufferCreateReady(allocator: kCFAllocatorDefault, dataBuffer: block, formatDescription: description,
                  sampleCount: count, sampleTimingEntryCount: 1, sampleTimingArray: &timing,
                  sampleSizeEntryCount: 0, sampleSizeArray: nil, sampleBufferOut: &sample) == noErr, let sample else {
        throw CaptureFailure("Synthetic audio sample failed")
    }
    try audioSample(sample)
}

let args = Array(CommandLine.arguments.dropFirst())
do {
    if args.first == "displays" {
        try writeJSON(["version":1, "displays":displays(), "sck_available":ProcessInfo.processInfo.operatingSystemVersion.majorVersion >= 14,
                       "hdr_capture_available":hdrAPIAvailable(), "screen_permission":CGPreflightScreenCaptureAccess()])
        exit(0)
    }
    if args.first == "synthetic" { try synthetic(args.count > 1 ? args[1] : "bt709"); exit(0) }
    if args.first == "synthetic-audio" { try syntheticAudio(args.count > 1 && args[1] == "planar"); exit(0) }
    if #available(macOS 14.0, *) {
        Task { do { try await capture(args) } catch { fail(error) } }
        RunLoop.main.run()
    } else { throw CaptureFailure("ScreenCaptureKit capture requires macOS 14 or later") }
} catch { fail(error) }
