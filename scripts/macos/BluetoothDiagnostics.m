// Read-only Classic Bluetooth diagnostics for a connected DualSense.
// Device addresses are compared internally and never included in reports.
#import <Foundation/Foundation.h>
#import <CoreBluetooth/CoreBluetooth.h>
#import <IOBluetooth/IOBluetooth.h>
#import <IOKit/hid/IOHIDManager.h>
#import <stdatomic.h>
#import <signal.h>
#import <fcntl.h>
#import <sys/stat.h>
#import <unistd.h>
#import <math.h>
#import <time.h>

@interface IOBluetoothDevice (GPSLinkDiagnostics)
- (uint8_t)connectionMode;
- (uint16_t)connectionModeInterval;
@end

static FILE *reportFile;
static int reportDescriptor = -1;
static BOOL debugEnabled;
static atomic_int workerFinished;
static int workerResult;

static double monotonicNow(void) {
    struct timespec value;
    clock_gettime(CLOCK_MONOTONIC, &value);
    return (double)value.tv_sec + (double)value.tv_nsec / 1e9;
}

static void emit(NSDictionary *record) {
    NSData *json = [NSJSONSerialization dataWithJSONObject:record options:0 error:NULL];
    if (!json) return;
    FILE *streams[] = {stdout, reportFile};
    for (NSUInteger i = 0; i < 2; i++) {
        if (!streams[i]) continue;
        flockfile(streams[i]);
        fwrite(json.bytes, 1, json.length, streams[i]);
        fputc('\n', streams[i]);
        fflush(streams[i]);
        funlockfile(streams[i]);
    }
}

static void checkpoint(NSString *name) {
    if (debugEnabled) emit(@{@"checkpoint": name, @"read_only": @YES});
}

static void workerTimedOut(int signalNumber) {
    (void)signalNumber;
    static const char message[] =
        "{\"status\":\"DIAGNOSTIC_TIMEOUT\",\"timeout_seconds\":12,\"read_only\":true}\n";
    (void)write(STDOUT_FILENO, message, sizeof(message) - 1);
    if (reportDescriptor >= 0) (void)write(reportDescriptor, message, sizeof(message) - 1);
    _exit(124);
}

static NSString *authorizationName(CBManagerAuthorization value) {
    switch (value) {
        case CBManagerAuthorizationNotDetermined: return @"not_determined";
        case CBManagerAuthorizationRestricted: return @"restricted";
        case CBManagerAuthorizationDenied: return @"denied";
        case CBManagerAuthorizationAllowedAlways: return @"allowed_always";
    }
    return @"unknown";
}

static void reportAuthorization(BOOL requested) {
    CBManagerAuthorization value = [CBManager authorization];
    emit(@{@"status": @"BLUETOOTH_AUTHORIZATION", @"authorization": @(value),
           @"authorization_name": authorizationName(value), @"permission_requested": @(requested),
           @"read_only": @YES});
}

static NSString *normalAddress(NSString *value) {
    if (![value isKindOfClass:NSString.class]) return nil;
    NSMutableString *result = [NSMutableString string];
    NSString *lower = value.lowercaseString;
    for (NSUInteger i = 0; i < lower.length; i++) {
        unichar character = [lower characterAtIndex:i];
        if ((character >= '0' && character <= '9') || (character >= 'a' && character <= 'f')) {
            [result appendFormat:@"%C", character];
        } else if (character != ':' && character != '-' && character != ' ') {
            return nil;
        }
    }
    return result.length == 12 ? result : nil;
}

static BOOL getterABI(id object, SEL selector, const char *expected) {
    NSMethodSignature *signature = [object methodSignatureForSelector:selector];
    return signature && signature.numberOfArguments == 2
        && strcmp(signature.methodReturnType, expected) == 0;
}

static BOOL linkGetterABI(IOBluetoothDevice *device) {
    return getterABI(device, @selector(isConnected), @encode(BOOL))
        && getterABI(device, @selector(isPaired), @encode(BOOL))
        && getterABI(device, @selector(connectionMode), @encode(uint8_t))
        && getterABI(device, @selector(connectionModeInterval), @encode(uint16_t))
        && getterABI(device, @selector(getConnectionHandle), @encode(uint16_t));
}

static NSDictionary *linkSnapshot(IOBluetoothDevice *device) {
    checkpoint(@"before_link_getters");
    BOOL connected = device.isConnected;
    BOOL paired = device.isPaired;
    uint8_t mode = device.connectionMode;
    uint16_t interval = device.connectionModeInterval;
    uint16_t handle = device.getConnectionHandle;
    checkpoint(@"after_link_getters");
    NSString *modeName = mode == 0 ? @"active" : mode == 1 ? @"hold"
        : mode == 2 ? @"sniff" : mode == 3 ? @"park" : @"unknown";
    return @{@"connected": @(connected), @"paired": @(paired), @"mode": @(mode),
             @"mode_name": modeName, @"interval_slots": @(interval),
             @"interval_ms": @(interval * 0.625), @"connection_handle": @(handle),
             @"values_valid_for_active_link": @(connected && handle <= 0x0eff)};
}

static int diagnose(double observeSeconds) {
    @autoreleasepool {
        // Every IOBluetooth call is behind this gate. No manager is created to acquire permission here.
        if ([CBManager authorization] != CBManagerAuthorizationAllowedAlways) {
            emit(@{@"status": @"BLUETOOTH_PERMISSION_UNAVAILABLE", @"read_only": @YES});
            return 3;
        }
        checkpoint(@"before_hid_enumeration");
        IOHIDManagerRef manager = IOHIDManagerCreate(kCFAllocatorDefault, kIOHIDOptionsTypeNone);
        if (!manager) { emit(@{@"status": @"HID_MANAGER_UNAVAILABLE", @"read_only": @YES}); return 1; }
        NSDictionary *matching = @{@kIOHIDVendorIDKey: @(0x054c),
                                  @kIOHIDProductIDKey: @(0x0ce6),
                                  @kIOHIDTransportKey: @"Bluetooth"};
        IOHIDManagerSetDeviceMatching(manager, (__bridge CFDictionaryRef)matching);
        IOReturn opened = IOHIDManagerOpen(manager, kIOHIDOptionsTypeNone);
        if (opened != kIOReturnSuccess) {
            CFRelease(manager);
            emit(@{@"status": @"HID_ENUMERATION_UNAVAILABLE", @"ioreturn": @(opened), @"read_only": @YES});
            return 1;
        }
        NSSet *devices = CFBridgingRelease(IOHIDManagerCopyDevices(manager));
        NSMutableSet<NSString *> *serials = [NSMutableSet set];
        for (id object in devices) {
            CFTypeRef serial = IOHIDDeviceGetProperty((__bridge IOHIDDeviceRef)object,
                                                     CFSTR(kIOHIDSerialNumberKey));
            if (serial && CFGetTypeID(serial) == CFStringGetTypeID()) {
                NSString *normalized = normalAddress((__bridge NSString *)serial);
                if (normalized) [serials addObject:normalized];
            }
        }
        IOHIDManagerClose(manager, kIOHIDOptionsTypeNone);
        CFRelease(manager);
        emit(@{@"status": @"DS5_HID_ENUMERATION", @"hid_devices": @(devices.count),
               @"valid_internal_serials": @(serials.count), @"read_only": @YES});
        if (devices.count != 1 || serials.count != 1) {
            emit(@{@"status": @"NEED_UNIQUE_BLUETOOTH_DS5", @"read_only": @YES});
            return 4;
        }

        NSString *targetAddress = serials.anyObject;
        checkpoint(@"before_paired_devices");
        NSArray<IOBluetoothDevice *> *paired = [IOBluetoothDevice pairedDevices] ?: @[];
        checkpoint(@"after_paired_devices");
        IOBluetoothDevice *selected = nil;
        NSUInteger matches = 0;
        NSUInteger connectedMatches = 0;
        for (IOBluetoothDevice *candidate in paired) {
            if (![normalAddress(candidate.addressString) isEqualToString:targetAddress]) continue;
            matches++;
            BOOL connected = candidate.isConnected;
            if (connected) connectedMatches++;
            if (!selected || connected) selected = candidate;
        }
        emit(@{@"status": @"PAIRED_CACHE_QUERY", @"target_matches": @(matches),
               @"connected_target_matches": @(connectedMatches), @"read_only": @YES});
        NSString *source = @"paired_cache";
        if (!selected) {
            NSMutableString *formatted = [NSMutableString string];
            for (NSUInteger i = 0; i < 6; i++) {
                if (i) [formatted appendString:@":"];
                [formatted appendString:[targetAddress substringWithRange:NSMakeRange(i * 2, 2)]];
            }
            checkpoint(@"before_address_lookup");
            selected = [IOBluetoothDevice deviceWithAddressString:formatted];
            checkpoint(@"after_address_lookup");
            if (![normalAddress(selected.addressString) isEqualToString:targetAddress]) selected = nil;
            source = @"address_lookup";
        }
        if (!selected) { emit(@{@"status": @"TARGET_OBJECT_UNAVAILABLE", @"read_only": @YES}); return 5; }
        if (!linkGetterABI(selected)) {
            emit(@{@"status": @"LINK_GETTER_ABI_UNAVAILABLE", @"read_only": @YES});
            return 5;
        }
        checkpoint(@"before_host_controller");
        IOBluetoothHostController *controller = [IOBluetoothHostController defaultController];
        BOOL powerABI = getterABI(controller, @selector(powerState), @encode(int));
        NSNumber *power = powerABI ? @([controller powerState]) : nil;
        emit(@{@"status": @"LEGACY_CONTROLLER", @"controller_available": @(controller != nil),
               @"power_abi_verified": @(powerABI), @"power_state": power ?: NSNull.null,
               @"read_only": @YES});

        NSDictionary *initial = linkSnapshot(selected);
        emit(@{@"status": [initial[@"values_valid_for_active_link"] boolValue] ? @"LINK_SNAPSHOT"
                    : @"TARGET_CONNECTION_UNAVAILABLE",
               @"source": source, @"getter_abi_verified": @YES, @"link": initial, @"read_only": @YES});
        double deadline = monotonicNow() + observeSeconds;
        while (observeSeconds > 0 && monotonicNow() < deadline) {
            [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.25]];
            emit(@{@"status": @"LINK_OBSERVATION", @"link": linkSnapshot(selected), @"read_only": @YES});
        }
        NSDictionary *last = linkSnapshot(selected);
        BOOL available = [last[@"values_valid_for_active_link"] boolValue];
        emit(@{@"status": available ? @"COMPLETE" : @"TARGET_CONNECTION_UNAVAILABLE",
               @"link": last, @"read_only": @YES});
        return available ? 0 : 5;
    }
}

@interface GPSPermissionObserver : NSObject <CBCentralManagerDelegate>
@end
@implementation GPSPermissionObserver
- (void)centralManagerDidUpdateState:(CBCentralManager *)central {
    emit(@{@"status": @"PERMISSION_MANAGER_STATE", @"manager_state": @(central.state),
           @"authorization": @([CBManager authorization]), @"read_only": @YES});
}
@end

static void pumpTimer(CFRunLoopTimerRef timer, void *info) { (void)timer; (void)info; }

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        BOOL requestPermission = NO;
        double observeSeconds = 0;
        NSString *outputPath = nil;
        for (int i = 1; i < argc; i++) {
            if (!strcmp(argv[i], "--request-permission")) requestPermission = YES;
            else if (!strcmp(argv[i], "--debug")) debugEnabled = YES;
            else if (!strcmp(argv[i], "--output") && i + 1 < argc)
                outputPath = [NSString stringWithUTF8String:argv[++i]];
            else if (!strcmp(argv[i], "--observe-seconds") && i + 1 < argc) {
                char *end = NULL;
                observeSeconds = strtod(argv[++i], &end);
                if (!end || *end || !isfinite(observeSeconds) || observeSeconds < 0 || observeSeconds > 8) {
                    emit(@{@"status": @"INVALID_OBSERVATION_DURATION", @"read_only": @YES}); return 2;
                }
            } else if (!strcmp(argv[i], "--help")) {
                puts("Read-only DS5 Bluetooth diagnostics.\n"
                     "  --output PATH           Save newline-delimited JSON (0600).\n"
                     "  --observe-seconds 0..8  Repeat link snapshots.\n"
                     "  --debug                 Include call checkpoints.\n"
                     "  --request-permission    Explicitly allow one OS permission request.\n"
                     "Launch the .app through LaunchServices for its own permission identity.");
                return 0;
            } else { emit(@{@"status": @"INVALID_ARGUMENTS", @"read_only": @YES}); return 2; }
        }
        if (outputPath) {
            int descriptor = open(outputPath.fileSystemRepresentation,
                                  O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW, 0600);
            if (descriptor < 0 || fchmod(descriptor, 0600) != 0) {
                if (descriptor >= 0) close(descriptor);
                emit(@{@"status": @"REPORT_FILE_UNAVAILABLE", @"read_only": @YES}); return 2;
            }
            reportFile = fdopen(descriptor, "w");
            if (!reportFile) { close(descriptor); return 2; }
            reportDescriptor = descriptor;
        }
        setvbuf(stdout, NULL, _IONBF, 0);
        reportAuthorization(NO);
        CBManagerAuthorization authorization = [CBManager authorization];
        BOOL permissionWasRequested = NO;
        __attribute__((objc_precise_lifetime)) GPSPermissionObserver *observer = nil;
        __attribute__((objc_precise_lifetime)) CBCentralManager *permissionManager = nil;
        if (authorization == CBManagerAuthorizationNotDetermined && requestPermission) {
            emit(@{@"status": @"PERMISSION_REQUEST_STARTED", @"permission_requested": @YES, @"read_only": @YES});
            permissionWasRequested = YES;
            observer = [GPSPermissionObserver new];
            permissionManager = [[CBCentralManager alloc] initWithDelegate:observer queue:nil options:nil];
            double deadline = monotonicNow() + 30;
            BOOL pendingReported = NO;
            while ((authorization = [CBManager authorization]) == CBManagerAuthorizationNotDetermined) {
                [[NSRunLoop currentRunLoop] runUntilDate:[NSDate dateWithTimeIntervalSinceNow:0.05]];
                if (!pendingReported && monotonicNow() >= deadline) {
                    emit(@{@"status": @"PERMISSION_PENDING", @"elapsed_seconds": @30,
                           @"waiting_for_user": @YES, @"read_only": @YES});
                    // Keep the app alive so its OS permission dialog is not dismissed by our timeout.
                    pendingReported = YES;
                }
            }
            reportAuthorization(YES);
        }
        if (authorization != CBManagerAuthorizationAllowedAlways) {
            NSString *status = authorization == CBManagerAuthorizationDenied ? @"BLUETOOTH_PERMISSION_DENIED"
                : authorization == CBManagerAuthorizationRestricted ? @"BLUETOOTH_PERMISSION_RESTRICTED"
                : @"BLUETOOTH_PERMISSION_NOT_DETERMINED";
            emit(@{@"status": status, @"permission_requested": @(permissionWasRequested),
                   @"iobluetooth_queries_skipped": @YES, @"read_only": @YES});
            return 3;
        }
        struct sigaction action = {0};
        action.sa_handler = workerTimedOut;
        sigemptyset(&action.sa_mask);
        sigaction(SIGALRM, &action, NULL);
        alarm(12);
        atomic_init(&workerFinished, 0);
        NSThread *worker = [[NSThread alloc] initWithBlock:^{
            @autoreleasepool {
                @try { workerResult = diagnose(observeSeconds); }
                @catch (NSException *exception) {
                    emit(@{@"status": @"OBJC_EXCEPTION", @"exception_type": exception.name, @"read_only": @YES});
                    workerResult = 1;
                }
                atomic_store_explicit(&workerFinished, 1, memory_order_release);
                CFRunLoopWakeUp(CFRunLoopGetMain());
            }
        }];
        worker.name = @"GamePadStudio Bluetooth diagnostic reader";
        CFRunLoopTimerRef timer = CFRunLoopTimerCreate(kCFAllocatorDefault,
            CFAbsoluteTimeGetCurrent() + 0.05, 0.05, 0, 0, pumpTimer, NULL);
        CFRunLoopAddTimer(CFRunLoopGetMain(), timer, kCFRunLoopDefaultMode);
        [worker start];
        while (!atomic_load_explicit(&workerFinished, memory_order_acquire))
            CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.1, true);
        CFRunLoopTimerInvalidate(timer);
        CFRelease(timer);
        alarm(0);
        // Preserve the permission manager/delegate for the complete main run loop lifetime.
        (void)permissionManager;
        (void)observer;
        if (reportFile) fclose(reportFile);
        return workerResult;
    }
}
